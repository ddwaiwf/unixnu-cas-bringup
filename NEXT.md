# 进度与续接（2026-11-10 02:20）

> 明天开机从这里接着干。

## 一句话状态

**XNU 内核在真机上完整跑到 BSD init 的根设备挂载。**

```
Failed to mount root device @IOKitBSDInit.cpp:984
frames fffffff00700797c35ed fffffff0078e48e0 ...
pages=0x00000000127192   free=0x00000000126957
```

与 `docs/.vitepress/device-records/fuxi-oddlylabs.toml` 的预测一字不差。
`pages`/`free` 比上一版（`0x12719a`）略小，正是内核多了 32 KB PMGR 桥接代码所致。

```
空目录 → 自编 clang → XNU 内核 → 8 MiB 固件 → 64 MiB payload → boot.img
→ UniXNU 菜单 ✅ → DarwinBoot 绿条 ✅ → ExitBootServices ✅
→ XNU 早期初始化 ✅ → AMFI 早期安全 ✅ → bsd_init ✅
→ IOFindBSDRoot ✅ → 根设备挂载 ⚠️   ← 当前
```

## 明天第一件事：让根设备可挂载

`docs/.vitepress/device-records/cas.toml` 里 `storage` 状态仍是 `planned`。
`board.toml [userdata]` 指向 LUN0（`sda34`）LBA `0x2c0000`–`0x3b4b7fa`、4 KiB 块，
那才是 iOS 根卷。

两条路：

1. **接 UFS / NativeUfs 驱动**，从真机 userdata 挂根。参考 `asteroids` / `sheng` 的
   `NativeUfs`、`ShengUfs` 实现和 `IOStorageFamily` 相关组件。
2. **先用 mockfs / ramdisk 挂最小根**，验证 `bsd_init` 之后的用户态路径
   （`CONFIG_XNU_SOURCE_MOCKFS=y` 已开，`build-mockfs-fixture.py` 已在 archives 里）。

## 今晚已完成、不用再做的事

### PMGR 桥接（AMFI 那关已过）

| 项 | 位置 |
|---|---|
| 源码 | `cas-full/package/ShengPlatformPower.cpp` 和 `cas-full/scripts/ShengPlatformPower.cpp` |
| 构建脚本 | `cas-full/scripts/build-cas-platform-power.py` |
| 开关 | `cas_defconfig` + `.config` 的 `CONFIG_XNU_SHENG_PMGR=y` |
| archives | `board.toml [kernel] archives` 末尾的 `"build-cas-platform-power.py"` |
| boot arg | `[payload] kernel_args` 里 `"sheng-pmgr=2"` |
| 钩子 | `sheng-platform-power.patch`（`bsd_init.c:779`）已在 arch 系列 |
| 产物 | `tree/build/sheng-platform-power/libShengPlatformPower.a` 255,136 字节 |

产物校验：340 项虚表 / 8 个绑定；`kernel.macho` 51,040,256 字节
（比无桥接版正好大 32,768 字节 = `-force_load` 生效）。

### Linux 适配（两个坑，已解决）

1. 参考脚本用 macOS `xcrun` → 换成编译记录里的 `clang-24` + `/usr/bin/llvm-ar`
2. WSL 的 `llvm-objcopy` 18 不支持 Mach-O → 用纯 Python 把
   `__DATA,__mod_init_func` 段描述符的 size/offset/reloff/nreloc 归零

详细代码和原理见 `README.md` §3.14。

### 已排除的无效路径

- 注释 prelink 的 `early_security_init` **不能**绕过 `AMFI: No PMGR?`
  （备份：`profiles/os/ipados-17.7.11-21H461/profile.toml.bak-amfi`）
- 该 profile.toml 的 `early_security_init` 目前仍是注释状态，要恢复可去掉注释

## 已有产物（都在，不用重编）

| 文件 | 说明 |
|---|---|
| `WSL:.../cas/kernel/kernel.macho` | 51,040,256 字节，含 PMGR 桥接 |
| `WSL:.../uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` | 8 MiB |
| `WSL:.../cas/payload/payload.img` | 64 MiB，含 `sheng-pmgr=2` |
| `D:\unixnu\dump\extract\boot-dtb-initrd.bin` | 478,636 字节，含 `linux,initrd-*` |
| `D:\unixnu\out\cas-darwinboot-v7.img` | 67,887,104 字节，sha256 `fb3124f5…`，**当前最好版本** |
| `D:\unixnu\out\cas-darwinboot-v5.img` / `v6.img` | 历史对照 |

重新打包（Windows 上直接跑，不用进 WSL）：

```bash
cd /d/unixnu && python tools/pack_boot.py \
  --fdt-blob dump/extract/boot-dtb-initrd.bin --index 0 \
  --shim release/ex/firmware/shim/shim.bin \
  --fd "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd" \
  --ramdisk "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/payload/payload.img" \
  --out out/cas-darwinboot-v8.img
```

## 刷机

```bash
# 长按电源 12 秒 → 音量下 + 电源 进 fastboot
cd /d/unixnu/tools/platform-tools
export MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1
./fastboot.exe devices
./fastboot.exe boot "D:/unixnu/out/cas-darwinboot-v8.img"
```

进菜单后按 Enter 或等 10 秒倒计时。`fastboot boot` 不写分区，长按电源 12 秒回 Android。

## 三条重要教训

1. **阶段条不可信**。`CasDisplayRefresh()` 会把 `Stopped` 置 1 并永久停止刷新
   （见 README §3.13）。真机画面才是唯一可信信号。
2. **「卡第 7 格」会偶发**。同一镜像第一次卡、第二次直接进内核（v6、v7 都验证过）。
   遇到就重刷一次，别急着改代码。
3. **格子里每格都画编号**，只能看颜色（青=到达，灰蓝=未到），不能看数字。

## 遗留

- DXE 冷启动偶发卡顿（约 50% 第一次）未定位，重刷可绕过。
- 存储 / 显示 / 输入驱动都没做。
- `cas-upstream.dsc` 的 stage bar 方向正常（横排在底部），之前误判是横着拍照。
