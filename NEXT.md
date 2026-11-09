# 进度与续接（2026-11-10 01:35）

> 明天开机从这里接着干。下面每一条都是可执行的。

## 一句话状态

固件在真机上跑到 **阶段 10 / 12（`CasStageKeys`）**，零报错、画面静止。
卡在 **DXE 驱动派发未完成**——某个驱动的 `EntryPoint` 没有返回，BDS 因此永远不会启动。

```
1 ShimValidated ✅   5 Mmu ✅          9 Gop ✅
2 FdCopied      ✅   6 Hobs ✅        10 Keys ✅  ← 当前
3 FdtCopied     ✅   7 Decompressed ✅ 11 Bds ⬜
4 Sec           ✅   8 Dxe ✅         12 Application ⬜
```

## 已有产物（都在，不用重编）

| 文件 | 说明 |
|---|---|
| `WSL:~/unixnu/src/UniXNU/out/cas/.../kernel/kernel.macho` | 48.6 MB，30 组件 / 69294 绑定 |
| `WSL:~/unixnu/src/uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` | 8 MiB，含两处修复 |
| `WSL:.../cas/payload/payload.img` | 64 MiB FAT32 |
| `D:\unixnu\dump\extract\boot-dtb-initrd.bin` | 478,636 字节，含 `linux,initrd-*` |
| `D:\unixnu\out\cas-darwinboot-v3.img` | 67,887,104 字节，sha256 `677643b5…`，当前上机版本 |

重新打包镜像（不需要 WSL，Windows 上直接跑）：

```bash
cd /d/unixnu && python tools/pack_boot.py \
  --fdt-blob dump/extract/boot-dtb-initrd.bin --index 0 \
  --shim release/ex/firmware/shim/shim.bin \
  --fd "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd" \
  --ramdisk "//wsl.localhost/Ubuntu-24.04/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/payload/payload.img" \
  --out out/cas-darwinboot-v4.img
```

## 明天的第一步：定位卡住的驱动

每轮约 1 分钟（编固件 20s + 打包 5s + 刷入 3s + 重启 30s）。

**实验 1** — 注释掉 `DarwinBoot.inf`，看能否进第 11 格：

```bash
# 文件：~/unixnu/src/uefi-cas/Platforms/Xiaomi/casPkg/Include/DXE.upstream.inc 第 28 行
#   INF DarwinBootPkg/Driver/DarwinBoot.inf      ← 前面加 #
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh fw
```

**实验 2** — 不行再注释 `GraphicsConsoleDxe.inf`（第 21 行），排除显示栈。

**实验 3** — 都不行就二分砍 `DXE.upstream.inc` 的驱动清单。

**更彻底**（推荐，一次定位）：给每个 `INF` 前后插入不同的 `CasDisplayMark` 阶段号。
需要临时扩展 `CasPlatform.h` 里的阶段枚举（现在只有 12 格）。

已经排除的：
- `CasKeysDxe` 明确 `return EFI_SUCCESS`
- `UniXNURamDiskDxe` 里没有 `while` / `for(;;)` / `Stall`

## 刷机流程（当前设备状态）

```bash
# 1. 设备卡死时先长按电源 12 秒断电
# 2. 音量下 + 电源 进 fastboot
cd /d/unixnu/tools/platform-tools
export MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1
./fastboot.exe devices
./fastboot.exe boot "D:/unixnu/out/cas-darwinboot-v4.img"
```

`fastboot boot` 只载入内存，不写分区。想回 Android：长按电源 12 秒再正常开机。

## 遗留问题（不阻塞）

- **阶段条竖向排列**：应该横向排在最底部，现在竖着贴在左边缘 →
  framebuffer 的宽高 / stride 方向不对，GOP 拿到的模式信息有误。
  等 DXE 跑通再修，修好在 `CasGopDxe.c` / `DisplayHardware.c`。

## 注意

- 设备屏幕**没有串口**，唯一的调试手段就是阶段条 + 故障码（见 README §5）。
- `fastboot` 和 `adb` 都能直接在本机跑，`wsl.exe` 在沙箱黑名单里，WSL 命令必须手敲。
- 修改 `casPkg` 后要**两侧同步**（`D:\unixnu\src\uefi-cas` 和 WSL 的 `~/unixnu/src/uefi-cas`），
  D 盘那份是源头，WSL 那份是编译用的。

## 已修的上游 bug

两个，都写在 `issues/` 下，可以直接提给 `ht94911/uefi-cas`：

- `issues/0001-sec-shared-initrd-called-before-copy.md`
- `issues/0002-dtb-chosen-missing-initrd-properties.md`

**这两个必须同时修**，只修一个仍然卡在同一阶段——这是最容易浪费时间的点。
