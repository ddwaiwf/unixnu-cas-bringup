# cas（小米 10 Ultra / SM8250）UniXNU + DarwinBoot 上机实录

> 2026-11-10 凌晨，在一台小米 10 Ultra（M2007J1SC，SM8250）上从**空目录**开始，
> 完整走通 UniXNU 的 `format 2` 构建链：自编 clang → 拉源码 → 编 XNU → 编 UEFI 固件 →
> 生成 ADT payload → 打包可启动镜像 → `fastboot boot` 上机。
>
> 最终固件在真机上跑到 **阶段 10 / 12（CasStageKeys）**，全程零报错。
> 期间定位并修复了上游两个从未在真机上暴露过的 bug。
>
> 这份文档把踩过的坑全部写下来，后来的人不用再白跑一遍。

---

## 0. 结果速览

```
阶段条：1-10 全绿，无 fault，画面静止
阶段 11 (Bds) / 12 (Application) 未到达
含义：DXE 驱动派发未完成 —— 某个驱动初始化后没有返回
```

`CasDisplayMark()` 会按颜色点亮阶段格，第 10 格之后卡住说明卡在 DXE 阶段，
不是 SEC / 内存 / 显示的问题。这两个我们已经跨过了。

---

## 1. 环境

| 项 | 值 |
|---|---|
| 宿主 | Windows 11（D 盘为数据盘，2TB 空闲） |
| WSL | Ubuntu 24.04，内核 6.18.54.1-microsoft-standard-WSL2 |
| CPU | AMD 9800X3D（16 线程） |
| 网络 | 国内，需要代理访问 GitHub；gitee 走直连 |
| 代理 | 本地混合端口 `127.0.0.1:7892` |
| 目标设备 | Xiaomi 10 Ultra `M2007J1SC`，SM8250，已解锁 bootloader |

仓库（全部 `git clone` 到 `D:\unixnu\src`，再 `cp -a` 进 WSL 的 `~/unixnu/src`）：

- `UniXNU/UniXNU` — 框架
- `ht94911/UniXNU-cas` — 设备仓库（board.toml / dts / package）
- `ht94911/uefi-cas` — Mu-Silicium 固件树
- `ht94911/UniXNU-sm8250-common` — SoC 公共层

---

## 2. 完整流水线

所有阶段由 `wsl-build.sh` 驱动，用法：

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh <stage>
# stage: doctor | setup | config | kernel | fw | payload | bootimg | status | probe
```

### 2.1 `setup` — 编 clang + 装 Linux 工具链（约 10 分钟）

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh setup
```

做的事：切换到阿里云 apt 源 → `sudo python3 scripts/linux/setup-toolchain.py`
→ 自编带 XNU 补丁的 clang（约 3136 步）→ 编 cctools-port(ld64)、libdispatch、
mig、iig、lzfse，全部装到 `/opt/unixnu-linux`。

**这一步有三个坑，见 §3.1 / §3.2 / §3.3。**

### 2.2 `config` — 拉源码（约 2.5 分钟，648 MB）

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh config
```

`unixnu defconfig cas_defconfig` + `./unixnu fetch`，拉 9 个源：

| 源 | 体积 |
|---|---|
| mu_basecore（+5 子模块） | 317.7 MB |
| xnu (10063.141.1) | 88.3 MB |
| libdispatch / libpthread / AvailabilityVersions / libplatform / corecrypto-2023 / IOStorageFamily / sm8250 | 共 ~7 MB |

成功的标志：

```
== wrote /home/<user>/unixnu/src/cas-full/unixnu.lock
```

### 2.3 `kernel` — 编 XNU + prelink

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh kernel
```

产出 `out/cas/<os-profile>/cas/kernel/kernel.macho`（48.6 MB）+ `kernel.json`。

验收标准（和仓库里其它设备的成功记录一致）：

```
30 个 kernelcache 组件，69294 个符号绑定
Mach-O: cf fa ed fe 0c 00 00 01 02 00 00 00 (arm64 execute)
```

**坑见 §3.4 / §3.5。**

### 2.4 `fw` — 编 EDK2 固件（约 20 秒）

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh fw
```

产出 `uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd`，
**恰好 8,388,608 字节 = 8.00 MiB**，正好填满 boot.img 的 FD 分区。

**坑见 §3.6 / §3.7。**

### 2.5 `payload` — 生成 ADT payload（64 MiB）

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/unixnu && \
  export UNIXNU_UNIQUE_CHIP_ID=0xd628f6b2805d91ce && \
  bash wsl-build.sh payload"
```

产出 `payload/payload.img` = 67,108,864 字节（正好 64 MiB，FAT32）。

**坑见 §3.8 / §3.9 / §3.10。**

### 2.6 `bootimg` — 打包可启动镜像

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/d/unixnu/wsl-build.sh bootimg
```

产出 `out/cas-darwinboot.img`（约 67.9 MB），Android boot header v2：

```
kernel  294,569 B @0x8000        （shim + 8 MiB FD 的 gzip）
ramdisk  64 MiB   @0x1000000     （payload.img）
dtb     478,636 B @0x1f00000     （含 linux,initrd-start/end，见 §3.11）
name    cas
```

**坑见 §3.12。**

### 2.7 上机

```bash
fastboot boot out/cas-darwinboot.img
```

`fastboot boot` 只载入内存、**不写任何分区**，失败长按电源 12 秒即可恢复。
实测发送 66,296 KB 用时 1.6 秒。

---

## 3. 踩坑清单（每条都是真实卡住过的）

### 3.1 ⚠️ `sudo` 会换掉「git 读哪个配置文件」的身份 —— 最贵的坑

**症状**：`setup-toolchain.py` 拉 llvm-project 时反复超时
`curl 28 ... Failed to connect to github.com port 443 after 129541 ms`。
明明已经配好了 gitee 镜像和代理。

**根因**：脚本用 `sudo python3 scripts/linux/setup-toolchain.py` 运行，
git 读的是 `/etc/gitconfig` 和 `/root/.gitconfig`，
而配置只写在了 `~/.gitconfig` —— **root 看不见**。

**修法**：system 级和 global 级都要写，并在结尾分别用两个身份验证。

```bash
M=https://gitee.com/mirrors/LLVM
O=https://github.com/llvm/llvm-project
sudo git config --system "url.$M.insteadOf" "$O"
sudo git config --system http.proxy  http://127.0.0.1:7892
sudo git config --system https.proxy http://127.0.0.1:7892
git  config --global "url.$M.insteadOf" "$O"
# 验证（两行必须一致）
git ls-remote --get-url "$O"
sudo git ls-remote --get-url "$O"
```

> 顺带一条经验：**拉大仓库前先花 10 秒验 commit 是否存在**，别拿 380 MB 去试错：
> `curl -s https://gitee.com/api/v5/repos/mirrors/LLVM/commits/<sha> | head -c 200`

### 3.2 ⚠️ Windows 上 clone 给 Linux 用的仓库 → `.patch` 变成 CRLF

**症状**：`git apply` 报 `patch does not apply`，但补丁内容和目标文件
**逐字符比对完全一致**，行号只差十几行（git apply 能容忍行号偏移）。

**根因**：`git clone` 在 Windows 上把 `.patch` 转成了 CRLF，目标源码是 LF。
每行末尾多一个 `\r`，上下文永远匹配不上。

**查法**（`grep -c $'\r'` 在 Git Bash 里会误报，必须用 `od`）：

```bash
head -1 某个.patch | od -c | head -2
```

**修法**：

```bash
sed -i 's/\r$//' 出问题的补丁文件
```

本次中招的是 `cas-full` 里的 3 个文件（UniXNU 主仓库有 `.gitattributes` 保护，
56 个 xnu 补丁都没事）：
`package/armcpu/darwin-asm-separators.patch`、`patches/xnu/cas-entry-trace.patch`、
`patches/xnu/cas-continuous-cpu0.patch`。

### 3.3 ⚠️ 中断过的 `cp -a` 会留下残缺目录，而 setup 有「目录存在就跳过」

**症状**：`fw` 报 `unixnu: /home/<user>/unixnu/src/uefi-cas is not a Mu-Silicium checkout`。

**根因**：之前某次 `cp -a` 被打断，`uefi-cas` 只拷进去 4 个条目（应有 16 个、2.5 GB）；
而 `stage_setup` 里的复制被 `if [ ! -d "$SRC" ]; then` 包着，后续重跑不会补。

**修法**：核对目录完整性再决定是否重拷。

```bash
ls -1 ~/unixnu/src/uefi-cas | wc -l   # 应该是 16
du -sh ~/unixnu/src/uefi-cas          # 应该是 2.5G
```

从 Windows 侧补齐（**不需要进 WSL**，`\\wsl.localhost` 就是网络路径）：

```powershell
robocopy "D:\unixnu\src\uefi-cas" "\\wsl.localhost\Ubuntu-24.04\home\<user>\unixnu\src\uefi-cas" `
  /MIR /MT:8 /R:1 /W:1 /NFL /NDL /NP
```

> 9p 通道是**按文件数**而非带宽受限的：8.3 万个小文件实测只有 2.7 MB/s。

### 3.4 ⚠️ 补丁序列缺前置补丁：`zzz-boot-progress` 打不上

**症状**：

```
XNU patch .../zzz-boot-progress.patch: git failed (1):
error: patch failed: bsd/kern/bsd_init.c:773
```

**根因**：`zzz-boot-progress.patch`（在 iPadOS OS 系列里）有一个 hunk 的上下文是

```c
#if defined(XNU_SHENG_PMGR) && XNU_SHENG_PMGR
  extern void sheng_pmgr_run(void);
  sheng_pmgr_run();
#endif
```

这个块由 `sheng-platform-power.patch` 创建，而 cas 的四个系列
（`arch` / `soc` / `os` / `device`）里**没有任何一个包含它**。

UniXNU 仓库自己的 `docs/.vitepress/device-records/sirius*.toml` 记录过同一类问题，
sdm710 那些设备后来把它放进了 SoC 系列，cas 这条没跟上。

**修法**：在 arch 系列末尾补一行（`profiles/arch/arm64/patches/xnu/series`）：

```
../../../../../patches/xnu/sheng-platform-power.patch
```

安全性：`.config` 里没有 `XNU_SHENG_PMGR`（未定义），那段代码被预处理器整块删掉。

### 3.5 ⚠️ 首次构建必然出现「Undefined symbols: _mockfs_mountroot」—— 这是设计如此

**症状**：

```
Undefined symbols for architecture arm64:
  "_mockfs_mountroot", referenced from: _vfstbllist in vfs_conf.o
```

**这不是错误。** `scripts/unixnu/kernel.py:196` 的注释写得很清楚：

> 首次从零构建时先跑一轮 `XNU_BOOTSTRAP=1`，跳过 archives 检查、**link 时不带 archives，
> 所以它故意停在链接这一步**，只为留下编译记录（`*.o.json`）给后面的 prelink 用。

第二轮（`startup.o.json` 已存在）会跳过这轮，直接用第一轮编好的目标文件链接，
所以 kernel 阶段第二次跑只要一分多钟。

### 3.6 ⚠️ `cas-upstream.dsc` 只存在于 uefi-cas 的 `main` 分支

**症状**：`fw` 阶段 `- Failed -`，`Build total time: 00:00:00`，**没有任何错误信息**。

**根因**：`board.toml` 指定 `dsc = "casPkg/cas-upstream.dsc"`，
而 `uefi-cas` 停在 `cas` 分支谱系的旧提交（`893f502`），`casPkg` 里只有 `cas.dsc`。

用 GitHub API 查三个分支：

| 分支 | 有 `cas-upstream.dsc` |
|---|---|
| `cas` | ❌ |
| `codex/cas-format2-001` | ✅ |
| `main` | ✅ |

**修法**：只取 `main` 的 `casPkg` 目录（65 MB 的 tar，解出 46 个文件）覆盖进去，
不必整树切换分支：

```bash
curl -L -o uefi-main.tar.gz \
  https://codeload.github.com/ht94911/uefi-cas/tar.gz/refs/heads/main
tar -xzf uefi-main.tar.gz -C ./out --strip-components=1 \
  uefi-cas-main/Platforms/Xiaomi/casPkg
cp -a ./out/Platforms/Xiaomi/casPkg/. ~/unixnu/src/uefi-cas/Platforms/Xiaomi/casPkg/
```

> 缺的不只是 `.dsc`，还有 `Include/Libraries.upstream.dsc.inc` 和
> `Include/Components.upstream.dsc.inc`，这两个是 `.dsc` 用 `!include` 引的。

### 3.7 ⚠️ EDK2 编译缺 `iasl`，`build_uefi.py` 缺 `coloredlogs`

`setup-toolchain.py` 的依赖清单里**没有** `acpica-tools`（EDK2 编 ACPI 表必需），
也没装 Python 的 `coloredlogs`（`build_uefi.py` 第 12 行直接 `import coloredlogs`）。

不装 sudo、不装 pip 也能补：

```bash
# iasl：从 deb 里直接抠二进制
curl -LO https://mirrors.tuna.tsinghua.edu.cn/ubuntu/pool/universe/a/acpica-unix/acpica-tools_20230628-1_amd64.deb
# 解 ar → data.tar.zst（注意是 zstd，Python 3.11 的 tarfile 不支持，用 Windows 的 bsdtar）
tar -xf acpica-tools_20230628-1_amd64.deb
tar -xf data.tar.zst ./usr/bin/iasl ./usr/bin/acpiexec
mkdir -p ~/.local/bin && cp usr/bin/iasl usr/bin/acpiexec ~/.local/bin/ && chmod +x ~/.local/bin/iasl
```

```bash
# coloredlogs：纯 Python，解 wheel 到用户级 site-packages 即可，不需要 pip
python -m pip download coloredlogs humanfriendly --no-deps -d wheels \
  --only-binary=:all: --platform manylinux2014_x86_64 --python-version 312
python -c "import zipfile,pathlib;d=pathlib.Path.home()/'.local/lib/python3.12/site-packages';d.mkdir(parents=True,exist_ok=True);[zipfile.ZipFile(w).extractall(d) for w in pathlib.Path('wheels').glob('*.whl')]"
```

> 为什么要 `--no-deps` 单下：`edk2-pytool-library` 带一个
> `sys_platform == "win32"` 的条件依赖（`pywin32`），pip 在 `--platform` 模式下
> 按宿主机判定，找不到 Linux 轮子就整批失败。而 `build_uefi.py` 其实只用 `coloredlogs`。

### 3.8 ⚠️ `payload` 需要平台种子，而它不在任何仓库里

**症状**：

```
unixnu: the platform seed is not stored in any repository:
        pass --unique-chip-id HEX (or UNIXNU_UNIQUE_CHIP_ID)
```

`board.toml` 里写着 `[payload.adt] unique_chip_id = "required"`。它最终只是写进 ADT
`/chosen` 的 `unique-chip-id`（u64）和 `chip-id`（u32），**本阶段不参与任何校验**。

**取值**：UniXNU 记录里其它设备用 `adb shell getprop ro.boot.chipecid`。
但小米这台的 bootloader **不暴露 64 位 ECID**：

| 属性 | 本机结果 |
|---|---|
| `ro.boot.chipecid` | 不存在 |
| `ro.boot.ecid` / `unique_chip_id` | 不存在 |
| `ro.boot.cpuid` | `0xd628f6b2`（32 位 CPU ID） |
| `ro.boot.serialno` | `805d91ce` |
| `fastboot getvar ecid` / `oem get_ecid` | `GetVar Variable Not Found` |

**可用替代**：把设备自身两个真实值拼成 u64，可复现、与硬件对应：

```bash
export UNIXNU_UNIQUE_CHIP_ID=0xd628f6b2805d91ce   # cpuid || serialno
```

### 3.9 ⚠️ ADT 交接只接受「单段连续 DRAM」，而 cas 有三段

**症状**：

```
unixnu: ADT handoff requires one contiguous DRAM range
```

`scripts/unixnu/devicetree.py:303`：

```python
if len(memory) != 1 or len(registers(memory[0])) != 1:
    raise Error('ADT handoff requires one contiguous DRAM range')
```

而 `cas.dts` 里 DRAM 是三段，中间有 103 MB 空洞：

| 段 | base | size | 结束 |
|---|---|---|---|
| 1 | `0x80000000` | `0x39900000` | `0xB9900000` |
| 2 | `0xC0000000` | 5 GiB | `0x200000000` |
| 3 | `0x200000000` | 6 GiB | `0x380000000` |

空洞 `0xB9900000`–`0xC0000000`（103 MB）**原本没有任何 reserved 区覆盖**。

**修法**：合并成一段，并把空洞显式声明为 `no-map` 保留区：

```dts
memory@80000000 {
    device_type = "memory";
    reg = <0x0 0x80000000 0x3 0x0>;      /* [0x80000000, 0x380000000) = 12 GiB */
};
reserved-memory {
    ranges;
    fixed-hole@b9900000 {
        reg = <0x0 0xb9900000 0x0 0x6700000>;
        no-map;
    };
    ...
};
```

改完要验证 owned 窗口（`0x100000000` + `0x130000000` = 4.75 GiB）与所有保留区**零冲突**。

### 3.10 🐛 上游 bug #1：`CasPrepareSharedInitrd` 在 `CopyMem` 之前调用

**这是本次最关键的发现。**

`casPkg/Sec/Sec.c` 原代码（仅 `CAS_UPSTREAM_PROFILE` 编译）：

```c
 62:  if (!CasPrepareSharedInitrd ((VOID *)FdtAddress, Info)) { Stop (EFI_COMPROMISED_DATA); }
 ...
 77:      CopyMem (CAS_PAYLOAD_BASE, Info->OriginalInitrd, Info->InitrdSize);
```

而 `CasPrepareSharedInitrd()` 第一件事就是读 `CAS_PAYLOAD_BASE` 的 FAT 引导签名：

```c
UINT8 CONST *Fat = (VOID *)(UINTN)CAS_PAYLOAD_BASE;
if (... || Fat[510] != 0x55 || Fat[511] != 0xaa) { return FALSE; }
```

**payload 要到 15 行之后才被拷进那个地址**，所以读到的是未初始化内存，必然失败，
SEC 随即 `Stop(EFI_COMPROMISED_DATA)` = **`0x8000000000000021`**。

**修法**：把调用挪到 `CopyMem` 之后。

> 这个 profile 在仓库记录里明确写着 "has not been booted on the phone yet"，
> 所以这条路径从来没被执行过，两个问题一直叠着。

### 3.11 🐛 上游 bug #2：`/chosen` 缺 `linux,initrd-start/end`

同一个函数还需要更新 `/chosen` 里的 initrd 地址，但：

```c
CONST VOID *Old = FdtGetProp (Fdt, Node, Name, &Length);
if (Old == NULL) { return FALSE; }        // 只「改」，不「加」
```

原厂小米 DTB 的 `/chosen` **只有 `bootargs`**，没有这两个属性 →
`CasSetExistingInitrd` 直接返回 FALSE。

**修法**：在 DTB 结构块里给 `/chosen` **插入**两个 8 字节属性。
注意 SEC 解析的是**启动镜像里那颗 DTB**（ABL 传进来的原厂 DTB），不是 `cas.dts`。

提供一个不经 dtc 反编译的最小插入器（避免任何语义漂移）：
`tools/patch_dtb_initrd.py`

```bash
python tools/patch_dtb_initrd.py --blob dump/extract/boot-dtb.bin --index 2 \
  --out dump/extract/boot-dtb-initrd.bin
```

它做的事：在结构块的 `/chosen` 处插入两个 `FDT_PROP`（值先给 `CAS_PAYLOAD_BASE`，
SEC 会改成实际起止），字符串块尾部追加属性名，然后修正
`totalsize` / `off_dt_strings` / `size_dt_struct` / `size_dt_strings`，最后补零 8192。

验证要点：改完必须能**完整遍历到 `FDT_END` 且深度归零**。

> 两个 bug 必须**同时**修。只补 DTB 属性没用——函数在 FAT 签名那一步就退出了，
> 根本走不到检查 FDT。我们第一轮只补了 DTB，结果仍然卡在同一个阶段。

### 3.12 ⚠️ `bootimg` 阶段找不到 FD

FD 在 `uefi-cas/Build/casUpstream001/DEBUG_CLANGPDB/FV/`，**不在 `UniXNU/out` 下面**，
而脚本原来只在 `$SRC/UniXNU/out` 里 `find`。按 `board.toml` 的 `fd` 路径直接找即可。

---

## 4. 现状与下一步

### 4.1 已经跨过的阶段

```
1 ShimValidated  ✅
2 FdCopied       ✅
3 FdtCopied      ✅
4 Sec            ✅  ← 修完上面两个 bug 后才过
5 Mmu            ✅
6 Hobs           ✅
7 Decompressed   ✅
8 Dxe            ✅
9 Gop            ✅
10 Keys          ✅  ← 当前
11 Bds           ⬜
12 Application   ⬜   DarwinBoot
```

### 4.2 卡点分析

停在 `Keys` 之后、`Bds` 之前，且**无 fault 条**，说明：

- 不是异常（异常会 `CasDisplayFault(Esr)`）
- 不是某个 `Stop(Status)`
- 而是**某个 DXE 驱动 EntryPoint 没有返回** —— BDS 要等所有驱动派发完才启动

`DXE.upstream.inc` 里 Keys 之后还有：

```
GraphicsConsoleDxe、UniXNUPkg/Application/UniXNU、
UniXNUDxe、UniXNURamDiskDxe、DiskIoDxe、EnglishDxe、Fat、DarwinBootPkg
```

已排除：

- `CasKeysDxe` 明确 `return EFI_SUCCESS`
- `UniXNURamDiskDxe` 内无 `while` / `for(;;)` / `Stall`

最可疑：**`DarwinBoot.inf`**（整条链里唯一从未在这台机器上跑过的组件）
和 **`GraphicsConsoleDxe`**（GOP 模式异常时可能死循环）。

### 4.3 明天的定位手法

每次循环约 1 分钟（编固件 20 秒 + 打包 5 秒 + 刷入 3 秒 + 重启 30 秒）：

1. 注释掉 `DXE.upstream.inc` 里的 `DarwinBoot.inf` → 能进第 11 格就是它
2. 不行就注释 `GraphicsConsoleDxe.inf` → 排除显示栈
3. 都不行就二分砍驱动清单

更彻底的做法：给每个 `INF` 前后插入不同的 `CasDisplayMark` 阶段号，
一次就能定位（阶段号只有 1-12，需要临时扩展枚举）。

### 4.4 已知但不阻塞的问题

- **阶段条竖向排列**（应横向排在最底部）→ framebuffer 的宽高 / stride 方向不对，
  说明 GOP 拿到的模式信息有误。不影响启动，等跑通再修。

---

## 5. 诊断工具（值得抄走）

### 5.1 阶段条解码

`CasDisplayMark(Stage)` 在屏幕底部画 12 个格子，**绿色 = 已到达，深蓝 = 未到达**，
格子里用 3×5 点阵字体写着阶段编号（十六进制）。

```c
Color = (S->Stages & (1u << I)) ? 0x16a070 : 0x303540;
```

阶段枚举在 `CasPlatform.h`：`ShimValidated=1 … Application=12`。

### 5.2 故障码

`CasDisplayFault(Error)` 画一条 300×30 的红条，旁边 16 个十六进制数字：

| 来源 | 特征 |
|---|---|
| `Sec.c` 的 `Stop(Status)` | EFI_STATUS，最高位为 1（如 `0x8000000000000021`） |
| `ArchSec.c` 的异常 | ESR 值（如 `0x96000004`） |
| `CasResetLib.c` 的复位 | 前缀 `0x5253540000000000` |

`0x21` = 33 = `EFI_COMPROMISED_DATA`，只有 SEC 里两处会抛。

### 5.3 别拿 `.vhdx` 增长当进度

WSL 的动态磁盘会复用已删除空间，增长不代表在下载。可靠做法：

```powershell
$a = (Get-NetAdapterStatistics | ? Name -eq "以太网 2").ReceivedBytes
Start-Sleep 5
$b = (Get-NetAdapterStatistics | ? Name -eq "以太网 2").ReceivedBytes
"下行 {0:N2} MB/s" -f (($b-$a)/5/1MB)
```

或者看日志文件的 `LastWriteTime` 和尾行（ninja 打印 `[  831/3136]`，git 打印
`Receiving objects: 46%`）。

> 正则注意：ninja 的进度数字是**右对齐带空格**的，必须写 `\[\s*(\d+)/(\d+)\s*\]`。

### 5.4 `\\wsl.localhost` 里看不见符号链接

`out/.../tree/package`、`tree/drivers/DarwinBoot` 都是符号链接，
从 Windows 侧看是空目录。别误判成「文件缺失」——`tree/package` 实际指向
`UniXNU/package`，`tree/drivers/*` 指向 `UniXNU/drivers/*`。

---

## 6. 本次改动的文件清单

| 文件 | 改动 |
|---|---|
| `UniXNU/profiles/arch/arm64/patches/xnu/series` | 末尾追加 `sheng-platform-power.patch` |
| `cas-full/dts/cas.dts` | 三段 DRAM 合并为单段 + 新增 `fixed-hole@b9900000` 保留区 |
| `uefi-cas/Platforms/Xiaomi/casPkg/` | 从 `main` 分支覆盖（补 `cas-upstream.dsc/.fdf` 及两个 `.inc`） |
| `uefi-cas/.../casPkg/Sec/Sec.c` | `CasPrepareSharedInitrd` 调用挪到 `CopyMem` 之后 |
| `cas-full/package/armcpu/*.patch` 等 3 个补丁 | CRLF → LF |
| `tools/patch_dtb_initrd.py` | 新增：往 DTB 的 `/chosen` 插入 initrd 属性 |

---

## 7. 时间账

| 阶段 | 耗时 |
|---|---|
| setup（自编 clang 3136 步） | 10 分钟 |
| config（648 MB 源码） | 2.5 分钟 |
| kernel（含 prelink） | 1–2 分钟（首轮 bootstrap 已编完目标文件） |
| fw（EDK2） | 20 秒 |
| payload | 约 1 分钟 |
| bootimg | 5 秒 |
| **纯构建合计** | **约 15 分钟** |

**其余 4 个多小时全花在排错上** —— 上面 §3 的每一条都是真金白银试出来的。

---

## 8. 给后来者的建议

1. **在 Windows 上准备 Linux 仓库时，先 `od -c` 查一遍 `.patch` 和 `.sh` 的换行。**
2. **任何 `sudo` 跑的脚本，git / pip / npm 配置都要写 system 级。**
3. **长任务失败两次就停下来定位根因**（身份 / 权限 / 网络 三选一），别第三次原样重试。
4. **构建链上的报错，先分清「设计如此」和「真的坏了」** —— bootstrap 停在 link 就是前者。
5. `cas-upstream.dsc` 这条路径此前从未上机，**遇到的两个 bug 都是真 bug**，
   值得给上游提 issue / PR。

---

*本文档由实际调试过程整理，所有命令均在 Windows 11 + WSL2 Ubuntu 24.04 上验证过。*
