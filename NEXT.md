# 进度与续接（2026-11-10 02:00）

> 明天开机从这里接着干。

## 一句话状态

**内核在真机上跑起来了。** UniXNU 菜单 → DarwinBoot 绿条 → ExitBootServices → XNU 执行 →
panic 在 `AMFI: No PMGR?`（`ConfigurationSettings.cpp:235`）。

```
空目录 → 自编 clang → XNU 内核 48.6 MB → 8 MiB 固件 → 64 MiB payload → boot.img
→ UniXNU 菜单 ✅ → DarwinBoot 交接 ✅ → XNU 早期初始化 ✅ → AMFI panic ⚠️
```

屏幕上那行 `pages=0x12719a  free=0x12619f` 是 XNU 内存管理器的统计，只有内核真的跑到那一步才会打印。

## 明天第一件事：绕过 AMFI 的 PMGR 检查

### 已经试过、确认无效的路

注释掉 `profiles/os/ipados-17.7.11-21H461/profile.toml` 里的

```toml
"early_security_init" = "com.apple.driver.AppleMobileFileIntegrity=0xfffffff005ab4ae0"
```

重新 prelink 后（日志里 `early-security` 出现 0 次，payload 内核哈希变成 `75cbe103…`），
panic **一模一样**。说明触发点在 **AMFI kext 自己的 ConfigurationSettings**，不是 prelink 的早期调用。

> 该行目前仍是注释状态，原文件备份在 `profile.toml.bak-amfi`。要恢复把注释去掉即可。

### 正确的路：造一个 provider 为 NULL 的 ApplePMGRNub

UniXNU 文档（sirius 移植记录）写得很清楚：

> AMFI is cleared without touching the target kernel: the nub helpers behind AMFI's
> readAon/writeAon return 0 when the provider stored at nub+0x88 is NULL, so publishing a
> real but provider-less ApplePMGRNub satisfies the latch (read 0, OR 4, write no-op).
> ... registers it from the `sheng_pmgr_run` hook in bsd_init
> (`CONFIG_XNU_SHENG_PMGR=1`, `sheng-pmgr=2`)

需要的四件东西：

1. `CONFIG_XNU_SHENG_PMGR=1`（`cas_defconfig` 里加）
2. boot arg `sheng-pmgr=2`（`board.toml [payload] kernel_args` 里加）
3. `sheng-platform-power.patch`（**已在补丁序列里**，今晚加的）
4. **ShengPlatformPower 的实现 + 构建脚本** ← **目前缺这个**

`cas-full/package/` 下现在只有 `armcpu/` 和 `Kit.toml`，没有平台电源的实现。
可以从别的移植抄：`sirius`（`package/SiriusPmgr.cpp`）或 `fuxi`
（`package/ShengPlatformPower.cpp` + `scripts/build-fuxi-platform-power.py`），然后：

- 实现放 `cas-full/package/platform-power/`
- 构建脚本写 `cas-full/scripts/build-platform-power.py`，产物落到
  `build/sheng-platform-power/libShengPlatformPower.a`
  （`build-xnu-4k-baseline.sh` 会强制加载这个路径）
- `board.toml` 的 `archives` 里加上这个脚本

## 已有产物（都在，不用重编）

| 文件 | 说明 |
|---|---|
| `WSL:.../cas/kernel/kernel.macho` | 48.6 MB，已去掉 AMFI 早期初始化 |
| `WSL:.../uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` | 8 MiB |
| `WSL:.../cas/payload/payload.img` | 64 MiB FAT32 |
| `D:\unixnu\dump\extract\boot-dtb-initrd.bin` | 478,636 字节，含 `linux,initrd-*` |
| `D:\unixnu\out\cas-darwinboot-v6.img` | 67,887,104 字节，**能进内核的那版** |
| `D:\unixnu\out\cas-darwinboot-v5.img` | 旧内核，同样能进内核（用于对照） |

重新打包镜像（Windows 上直接跑，不用进 WSL）：

```bash
cd /d/unixnu && python tools/pack_boot.py \
  --fdt-blob dump/extract/boot-dtb-initrd.bin --index 0 \
  --shim release/ex/firmware/shim/shim.bin \
  --fd "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd" \
  --ramdisk "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/payload/payload.img" \
  --out out/cas-darwinboot-v7.img
```

## 刷机流程

```bash
# 设备卡死时先长按电源 12 秒断电，然后 音量下 + 电源 进 fastboot
cd /d/unixnu/tools/platform-tools
export MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1
./fastboot.exe devices
./fastboot.exe boot "D:/unixnu/out/cas-darwinboot-v7.img"
```

进菜单后按 Enter（或等 10 秒倒计时）引导。
`fastboot boot` 不写分区，长按电源 12 秒可回 Android。

## 重要教训（今天踩的）

### 1. 别信阶段条

`CasDisplayRefresh()` 一旦发现 CTL 配置被 GOP 改过，就置 `Stopped=1` 并永久停止刷新，
之后所有阶段标记静默丢弃。这让我们误判了两轮——「卡在第 7 格」其实系统早就跑到菜单了。

已修（`DisplayHardware.c`）：

- 刷新失败只累加 `RefreshErrors` 然后 `continue`，不再置 `Stopped`
- `CasDisplayMark` 的守卫里去掉了 `S->Stopped`

### 2. 单次现象不能当结论

v6 第一次启动卡在第 7 格，第二次直接进了内核。**同镜像、同设备、不同结果。**

### 3. 阶段条怎么读

12 个格子，青色 `0x16a070` = 已到达，深蓝 `0x303540` = 未到达。
**每格都会画十六进制编号（1..C）**，所以只能看**颜色**，不能看数字。

## 遗留

- DXE 阶段的偶发卡顿（第 7 格）仍存在，未定位。
- 存储 / 显示 / 输入驱动都没做，AMFI 过了之后还有很长的路。
