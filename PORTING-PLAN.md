# alioth → cas 移植清单（目标：桌面）

> 生成于 2026-11-10 02:25。**这份文档里的分析、克隆、比对、脚本准备全部可以在无人值守时完成**；
> 只有「跑 WSL 命令」「按电源键」「拍屏幕」需要人。

## 0. 现状与目标

当前 cas（小米 10 Ultra / SM8250 / ipados-17.7.11-21H461）：

```
Failed to mount root device @IOKitBSDInit.cpp:984
```

XNU 已完整启动到 BSD init 的根设备挂载。启动链的问题全部解决，**剩下的是设备驱动和用户态**。

目标分两档：

| 档 | 含义 | 参考状态 |
|---|---|---|
| A. 看到桌面被画出来 | launchd + 锁屏 + GPU 出图 | `alioth` 已做到 |
| B. 稳定可用桌面 | SpringBoard 不崩、有网络/音频/电源 | `alioth` **自己也还没做到** |

## 1. 参考仓库（已就位）

```
D:\unixnu\src\alioth          git: https://github.com/taishang1024/UniXNU-alioth.git
```

90 个文件：`board.toml`、`configs/alioth_defconfig`、`package/`（32 个）、
`scripts/`（16 个）、`dts/alioth.dts`、`drivers/`、`kits/`、`patches/`。

**为什么选它**：与 cas 同为 **SM8250** SoC、**ipados-17.7.11-21H461** OS profile。
`docs/.vitepress/device-records/alioth.toml` 记录：

> UFS root with launchd, a stable lock screen, touch input and a published GPU device
> driving the staged Metal accelerator; **the desktop draws** on the parity pin but
> SpringBoard loses its Metal device at the client IOVA window, and there is no Wi-Fi.

## 2. 差距表

| 区域 | cas | alioth | 要做的事 |
|---|---|---|---|
| configuration | implemented | implemented | 参考其 `[components]` / `[ipsw]` |
| firmware | device-verified | implemented | 已够用 |
| cpu | device-verified | implemented | 已够用 |
| memory | implemented | implemented | 已够用 |
| **storage** | **planned** | **device-verified** | 搬 `ShengUfs` + storage bindings |
| **display** | **planned** | implemented | 搬 `GopFramebuffer` / legacy display |
| **input** | **planned** | implemented | 搬 `ShengHidInput` + 触控板级驱动 |
| **gpu** | **planned** | implemented | 最大块，`NativeAdreno*` / A650 |
| network / audio / power | planned | planned | 两边都没做，跳过 |
| packaging | implemented | device-verified | 需补 Data seed / trust cache / union |
| diagnostics | planned | device-verified | 可选 |

统计：`cas` = 2 device-verified / 3 implemented / **8 planned**；
`alioth` = 3 / 7 / **3**。差 5 个区域。

## 3. 组件可用性（框架 `UniXNU/components.toml` 已确认）

| 组件 | 框架里有？ | 用途 |
|---|---|---|
| `ShengUfs` | ✅ | UFS 传输 |
| `ShengHidInput` | ✅ | HID 输入 |
| `ShengNovatekTouch` | ✅ | Novatek 触控 IC（**cas 的 IC 待确认**） |
| `VirtioMmioBlock` | ✅ | 根认证用 |
| `RamBlock` | ✅ | Data 卷走 RAM 块设备 |
| `GopFramebuffer` | ✅ | 显示 |
| `AppleKeyStoreState` | ✅ | Data seed 前置 |
| `Adreno740` | ✅ | 框架只到 740，**cas 是 A650** |
| `QcomXnuUsbDebug` | ❌ | alioth 设备仓私有 |
| `Adreno650` | ❌ | alioth 设备仓私有（`drivers/Adreno650`） |

结论：存储/显示/输入/keystore **都是框架现成组件，只需在 cas 里选中**；
GPU 是唯一需要从 alioth 设备仓搬整套源码的。

### 3.1 ⚠️ 关键差异：alioth 用的是另一套 feed

两边 `feeds.toml` **不是同一个仓库**：

| 源 | cas | alioth |
|---|---|---|
| `sm8250` | `ht94911/...@0c13f177` | `FFT-infra/...@a6e7e36c` |
| `ShengUfs` | 无 | `FFT-infra/NativeUfs-alioth@ab54059` |
| `GopFramebuffer` | 无 | `FFT-infra/GopFramebuffer@b06fd74` |
| `Adreno740`（实际 A650） | 无 | `FFT-infra/Adreno650@bdee54c` |
| `QcomXnuUsbDebug` | 无 | `FFT-infra/QcomXnuUsbDebug@18bcc38` |

**已全部预拉到 `D://unixnu//src//ref//`**（5 个仓库、388 个文件），明天不用等下载。

影响与建议：

1. **不要整体换掉 cas 的 `sm8250` soc 源**——那会让补丁序列、DTS、内存映射全部失效，
   今晚跑通的链路会白费。正确做法是把 `NativeUfs-alioth` 等**作为额外源加入**，
   逐个组件搬。
2. `build-storage-bindings.py` **不在 UniXNU 框架、alioth/scripts 或上述 5 个仓库里**，
   它来自 alioth 的某个 kit（设备仓 `kits/` 里有 gitlink 占位）。
   框架里有语义相近的 `tree/scripts/build-sheng-source-storage.py`，
   明天要先确认该用哪个，别直接把 alioth 的 archives 抄过来就跑。
3. 因此「阶段 1」的实际工作量比表格里略大，**估计上调 1–2 晚**。

### 3.2 预置状态（无人值守已完成）

- `D://unixnu//src//alioth` —— 参考设备仓（90 文件）
- `D://unixnu//src//ref//UniXNU-sm8250-common` —— 47 文件
- `D://unixnu//src//ref//NativeUfs-alioth` —— 72 文件
- `D://unixnu//src//ref//GopFramebuffer` —— 40 文件
- `D://unixnu//src//ref//Adreno650` —— 136 文件
- `D://unixnu//src//ref//QcomXnuUsbDebug` —— 93 文件

**没有改动任何正在工作的构建树**，这些只是参考资料。

---

## 4. 四个阶段（按顺序做，每阶段都有验收）

### 阶段 1：挂根（1–2 晚）

**改 `cas-full/configs/cas_defconfig`**，开：

```ini
CONFIG_XNU_NATIVE_UFS=y
CONFIG_XNU_NATIVE_RAM_BLOCK=y
CONFIG_XNU_NATIVE_VIRTIO_BLOCK=y
CONFIG_XNU_VERIFIED_ROOT_PROBE=y
CONFIG_XNU_OWNER_ROOT_AUDIT=y
CONFIG_XNU_OWNER_ROOT_TRUST=y
CONFIG_XNU_OWNER_BOOT_MANIFEST=y
CONFIG_XNU_OWNER_DATA_MOUNT=y
CONFIG_XNU_OWNER_CACHE_MOUNT=y
CONFIG_XNU_OWNER_CACHE_STARTUP=y
CONFIG_XNU_OWNER_UNION_SYSTEMOS=y
CONFIG_XNU_OWNER_DATA_VOLUME_UUID="B0B12459-4F23-4E03-9B64-A902959309AA"
CONFIG_XNU_EXTRA_LD_ARCHIVES="{tree}/build/link-stubs/libLinkStubs.a"
```

**改 `board.toml [kernel] archives`**，按 alioth 的顺序插入：

```
"component:ShengUfs",
"build-storage-bindings.py --component NativeUfs --archive libShengUfs.a --directory build/sheng-ufs",
"component:VirtioMmioBlock",
"build-storage-bindings.py --component NativeVirtioBlock --archive libVirtioMmioBlock.a --directory build/virtio-mmio-block --subset",
"component:RamBlock",
"build-storage-bindings.py --component NativeRamBlock --archive libRamBlock.a --directory build/ram-block --subset",
"build-link-stubs.py",
"component:AppleKeyStoreState",
"build-platform-power.py"   ← 我们已有等价物 build-cas-platform-power.py，别重复
```

**改 `board.toml [components]`**：加 `include = ["ShengUfs", "ShengHidInput", "VirtioMmioBlock", "RamBlock", "GopFramebuffer", "AppleKeyStoreState"]`。

**版级参数 cas 已经量好**（`board.toml [userdata]`）：

```toml
first_lba = 0x2c0000        # alioth 是 0x2a8000
last_lba  = 0x3b4b7fa       # alioth 是 0x3b037fa
lba_bytes = 4096
windows = [OS, SystemOS]    # 与 alioth 相同
```

UFS host `0x1d84000`（见 `cas.toml` storage inputs）。`UniXNU-umi` 有同 SoC 的
SM8250 UFS 适配，是 `ShengUfs` 移植的参考。

**验收**：屏幕上 `Failed to mount root device` 消失，出现 launchd 相关输出或新的 panic。

### 阶段 2：launchd 起来（2–3 晚）

`payload` 阶段还需要三个仓库里没有的输入（alioth 记录里明确写了）：

```
--trust-cache <owner bundle>
--data-ramdisk <seed>
--gpu-addon <addon>
```

alioth 的 `scripts/` 里有 `find-cache-image.py`、`make-gpu-addon-alioth.sh`，
`package/` 里有 `KeyStoreProbe.cpp`。需要为 cas 生成对应的 seed / trust bundle。

**验收**：`launchd` 打印启动信息，锁屏出现（哪怕没有 GPU 也能出基础 UI）。

### 阶段 3：显示 + 触摸（3–5 晚）

- `CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`
- archives 加 `"build-gop-legacy-display.py"`、`"build-alioth-touch.py"`（改名）
- `component:ShengHidInput`
- **必须重新抓 cas 的板级参数**：面板时序、背光、触控 IC 型号。
  alioth 用 Novatek（`ShengNovatekTouch`），cas 的触控 IC 未确认。
  可用今晚那套 adb 方法读 `/sys/class/...`、`getprop ro.boot.*`、DTB 节点。

**验收**：锁屏可见，触摸有响应。

### 阶段 4：GPU / Metal（3–7 晚，最不确定）

- `CONFIG_XNU_NATIVE_ADRENO=y`
- 从 `alioth/drivers/Adreno650` + `package/GpuFirmwareA650.c` + `GpuProbe.cpp` 等整套搬
- `scripts/build-adreno650.py`、`make-gpu-addon-alioth.sh`
- 框架只有 `Adreno740`，A650 没有 catalog component，必须自带

**风险**：alioth 自己在这步仍有
`SpringBoard loses its Metal device at the client IOVA window`，
并有专门的 `check-gpu-client-windows.py` / 大量 GPU slot 调参记录。

## 5. 时间估计（按每晚 2–3 小时）

| 档 | 乐观 | 现实 |
|---|---|---|
| A. 看到桌面画出来 | 7–10 晚 | **2–4 周** |
| B. 稳定可用桌面 | 不承诺 | alioth 至今未做到 |

另外还欠一个**前置稳定性问题**：冷启动偶发卡第 7 格（约 50% 第一次），
建议先修再开始阶段 1，否则每轮验证都要重刷两次。

## 6. 无人值守能做 / 不能做

**能做**（已完成或可继续）：

- clone / 读源码 / diff 配置 / 生成清单 ✅ 本文档
- 改文件、写脚本、打补丁、提交本地 git
- 跑 `git` / `python` / `curl` / `adb` / `fastboot`
- 在不触碰 WSL 的前提下重新打包 `boot.img`（`pack_boot.py` 是纯 Python）

**不能做**：

| 事 | 原因 |
|---|---|
| 跑 `wsl-build.sh kernel / fw / payload` | `wsl.exe` 在沙箱程序黑名单里 |
| 长按电源 12 秒 / 音量下+电源 | 物理按键 |
| 看屏幕、拍照片 | 没有视觉输入 |
| 确认启动结果 | 依赖上面三项 |

**所以每次迭代最少需要人做 3 件事**：敲一条 WSL 命令、把手机弄进 fastboot、拍一张屏幕。

## 7. 明天第一件事（可直接执行）

```
# 1. 看这份清单和参考仓库
cat D:\unixnu\repo-cas-bringup\PORTING-PLAN.md
diff -u D:\unixnu\src\cas-full\configs\cas_defconfig D:\unixnu\src\alioth\configs\alioth_defconfig

# 2. 先修冷启动偶发卡 7（可选但强烈建议）
#    见 README §4.2 / NEXT.md

# 3. 按「阶段 1」改 defconfig + board.toml，然后
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/unixnu && export UNIXNU_UNIQUE_CHIP_ID=0xd628f6b2805d91ce && bash wsl-build.sh kernel && bash wsl-build.sh payload"
```

## 8. 参考资料位置

| 资料 | 路径 |
|---|---|
| cas 设备记录 | `UniXNU/docs/.vitepress/device-records/cas.toml` |
| alioth 设备记录 | `UniXNU/docs/.vitepress/device-records/alioth.toml` |
| 最成熟的 SM8250 移植记录 | `.../alioth-taishang1024.toml`（含 GPU slot 调参） |
| AMFI/PMGR 契约 | `UniXNU/scripts/audit-amfi-pmgr-contract.py` |
| 今晚的实录与踩坑 | 本仓库 `README.md`、`NEXT.md`、`issues/` |
