# cas（小米 10 Ultra / SM8250）UniXNU + DarwinBoot 上机实录

> 2026-11-10 凌晨，在一台小米 10 Ultra（M2007J1SC，SM8250）上从**空目录**开始，
> 完整走通 UniXNU 的 `format 2` 构建链：自编 clang → 拉源码 → 编 XNU → 编 UEFI 固件 →
> 生成 ADT payload → 打包可启动镜像 → `fastboot boot` 上机。
>
> 最终 XNU 内核在真机上执行到 **BSD init 的根设备挂载**：
> `Failed to mount root device @IOKitBSDInit.cpp:984`。
> 期间定位并修复了上游两个从未在真机上暴露过的 bug，并把 PMGR 桥接移植到了 Linux 工具链。
>
> 这份文档把踩过的坑全部写下来，后来的人不用再白跑一遍。

---

## 0. 结果速览

```
UniXNU 菜单 ✅ → DarwinBoot 绿条 ✅ → ExitBootServices ✅
→ XNU 早期初始化 ✅ → AMFI 早期安全 ✅（PMGR 桥接）
→ bsd_init ✅ → IOFindBSDRoot ✅ → 根设备挂载失败 ⚠️

屏幕最终输出：
Failed to mount root device @IOKitBSDInit.cpp:984
frames fffffff00700797c35ed fffffff0078e48e0 ...
pages=0x00000000127192   free=0x00000000126957
```

这不是崩溃，是**内核完整启动到了挂载根文件系统**这一步——存储驱动还没做，
所以找不到根设备。与 `fuxi-oddlylabs.toml` 记录的预测结果一字不差。

> ⚠️ 途中一度以为「卡在阶段 7 / 10」。**阶段条不可信**：`CasDisplayRefresh()`
> 会把 `Stopped` 置位并永久停止刷新（见 §3.13）。真机画面才可信。

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

### 3.13 ⚠️ 阶段条会自己停掉 —— 本次最大的误导

`CasDisplayRefresh()` 在 `Library/CasDisplayLib/DisplayHardware.c`：

```c
if (Read (B + 0xf4) != State->Snapshot.Ctls[I].IntfActive ||
    Read (B + 0x14) != State->Snapshot.Ctls[I].Top) {
  ++State->RefreshErrors; State->Stopped = 1; return;
}
```

GOP 接管时 CTL 配置会被改写，于是 `Stopped` 被置位；而 `CasDisplayMark()`
的守卫里有 `S->Stopped`，此后**所有阶段标记静默丢弃**。结果就是：

> 条子停在 7（或 10），画面不动，而系统其实早就跑到 UniXNU 菜单甚至内核了。

**修法**（两处，均已验证）：

```c
// 刷新失败只计数、不放弃
++State->RefreshErrors; continue;
// CasDisplayMark 守卫里去掉 S->Stopped
if (S->Magic != CAS_DISPLAY_MAGIC || !S->Ready || Stage == 0 || ...) { return; }
```

**教训**：阶段条只是辅助，真机画面才是唯一可信的信号。
另外 12 个格子里**每格都会画十六进制编号（1..C）**，只能看颜色，不能看数字。

### 3.14 ⚠️ 把 macOS 的 PMGR 桥接脚本移植到 Linux

`XNU` 在 AArch64 高通平台上会 panic 在 `AMFI: No PMGR?`，需要一个
provider 为 NULL 的 `ApplePMGRNub`。参考 `UniXNU-sheng@08cde20` 的
`ShengPlatformPower.cpp` 与 `OddlyLabs/UniXNU-fuxi` 的构建脚本，两处需要适配：

**(a) `xcrun` 是 macOS 专属。**

- 汇编文件（`original-bindings.S`）只有 `.globl` / `.set`，不需要 SDK：
  直接用编译记录 `IOPlatformExpert.cpo.json` 里的
  `/opt/unixnu-linux/llvm/bin/clang-24 --target=arm64-apple-ios17.0 -c`
- `xcrun ar rcs` → `/usr/bin/llvm-ar rcs`

**(b) WSL 的 `llvm-objcopy` 18 不支持 Mach-O。**

```
/usr/bin/llvm-objcopy: error: option is not supported for MachO
  --remove-section=__DATA,__mod_init_func
  --globalize-symbol=__GLOBAL__sub_I_ShengPlatformPower.cpp
```

用纯 Python 改目标文件即可：把 `__DATA,__mod_init_func` 的段描述符
（`LC_SEGMENT_64` 里 80 字节一节）`size` / `offset` / `reloff` / `nreloc` 四个字段归零，
链接器就不会收集 C++ 全局构造器。文件长度不变，`__text` 不变，脚本后面的
`text_section(raw) == text_section(late)` 自检照样过。

`__GLOBAL__sub_I_*` 保持局部符号即可——`sheng_pmgr_run()` 对它的调用通过
目标文件内的重定位直接指向该符号，不需要 globalize。

完整脚本：`cas-full/scripts/build-cas-platform-power.py`。

---

## 4. 现状与下一步

### 4.1 已经跨过的阶段

```
1 ShimValidated  ✅   7 Decompressed ✅
2 FdCopied       ✅   8 Dxe ✅
3 FdtCopied      ✅   9 Gop ✅
4 Sec            ✅  10 Keys ✅
5 Mmu            ✅  11 Bds ✅  UniXNU 菜单
6 Hobs           ✅  12 Application ✅  DarwinBoot 绿条 → XNU
```

### 4.2 两个曾经被误判的「卡点」

排查过程中有两轮被自己的仪表骗了，记录下来免得后来者重蹈覆辙：

1. **「卡在第 7 / 10 格」**：`CasDisplayRefresh()` 一旦发现 CTL 配置被 GOP 改过，
   就置 `Stopped = 1` 并永久停止刷新，之后所有阶段标记静默丢弃。
   系统其实早就跑到菜单甚至内核了。**真机画面才是唯一可信的信号。**
   修法见 §3.13。
2. **「阶段条竖向排列」**：那是拍照时手机横着拿，不是 framebuffer 方向错误。
   竖屏照片里它就是横排在最底部的，完全正常。

另外，「卡第 7 格」还**会偶发**：同一个镜像第一次卡、第二次直接进内核。
单次现象不能当结论。

### 4.3 已经修掉的 AMFI 关卡

XNU 起初 panic 在

```
"AMFI: No PMGR?\n" @ConfigurationSettings.cpp:235
```

高通平台没有 Apple PMGR。试过注释 prelink 的 `early_security_init`，**无效**——
触发点在 AMFI kext 自己的 `ConfigurationSettings`。

正确解法（与 `fuxi-oddlylabs.toml` 一致）：发布一个 provider 为 NULL 的
`ApplePMGRNub`。完整做法见 §3.14 和 `NEXT.md`。修完后屏幕变成：

```
Failed to mount root device @IOKitBSDInit.cpp:984
```

### 4.4 下一步：根设备挂载

`cas.toml` 的 `storage` 区域状态仍是 `planned`。`board.toml [userdata]` 指向
LUN0（sda34）LBA `0x2c0000`–`0x3b4b7fa`、4 KiB 块，那才是 iOS 根卷所在。

- 要么接上 UFS / NativeUfs 驱动，从真机 userdata 挂根
- 要么先用 mockfs / ramdisk 挂一个最小根，验证 `bsd_init` 之后的用户态路径

这一步开始就是「让 Darwin 真正跑起来」的活了，不再是启动链的问题。

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

## 9. 2026-11-10：换 Mac 原生构建，真机第一次跑起用户态进程（PID 1）

第二天把整条链搬到 **Mac（Apple Silicon, macOS 27.2）** 重建，全部原生（无 WSL），
当天达成 **AMFI 验签 → mockfs RAM 根 → /sbin/launchd 被 exec → PID 1 正常运行退出**。
本文档 §1–8 记录的是 Windows/WSL 首日；本节是 Mac 接续日，两条路径现已都能出可上机镜像。

### 9.1 Mac 工具链（复现清单）

```bash
brew install cmake ninja unifdef llvm lld mtools dtc lzfse   # 清华 bottle 镜像加速
# 完整 Xcode（App Store），许可同意后 iphoneos SDK 可用——kernel 步骤硬依赖
curl -LO https://dl.google.com/android/repository/platform-tools-latest-darwin.zip  # fastboot/adb
```

克隆四仓：`UniXNU`（主仓+子模块）、`UniXNU-cas`、`uefi-cas`（main 分支）、
`UniXNU-sm8250-common`，另加参考仓 `UniXNU-sheng` / `UniXNU-alioth`。

**Mac 特有坑：**
1. WorkBuddy 等工具注入的 `PYTHONPATH` 会把 `mkdir(exist_ok=True)` 拦成 EEXIST
   → 跑 `./unixnu` 前 `export PYTHONPATH=`。
2. `prepare-xnu-source.py` 调 GitHub API（DriverKit/TrustCache 列表），共享代理出口 IP
   撞匿名限流 → **直连**预置三份缓存 JSON 到 `tree/.cache/sources/`。
3. SafeDelete 类文件策略会咬 XNU Makefile（sysent 重建 >50 文件）→ 内核构建在可信环境跑。

### 9.2 ⚠️ 最重要的一个坑：`PcdDebugPropertyMask` 必须 `0x0F`（Windows 端改了没写进来）

`cas-upstream.dsc` 上游值 `0x2F` 含 `ASSERT_BREAKPOINT|ASSERT_DEADLOOP` 位：
**任何驱动一旦 ASSERT()，直接 CpuBreakpoint → 异常 → 死循环，屏幕阶段条永远冻结**，
且没有任何文字输出。首日 Mac 构建的镜像三次上机全部冻在第 10 格（Keys）就是它。
Windows 端第一天就手改成了 `0x0F`（ASSERT 打印后继续），但没记录。

```diff
-  gEfiMdePkgTokenSpaceGuid.PcdDebugPropertyMask|0x2F
+  gEfiMdePkgTokenSpaceGuid.PcdDebugPropertyMask|0x0F
```

**教训：跨机器接续时，先把上一台机器「改了但没写进文档」的配置 diff 一遍**
（对照 `.bak-*` 备份文件），再谈重建。

### 9.3 ⚠️ 第二个坑：根设备参数名是 `rd=` 不是 `root=`

XNU 的 `IOKitBSDInit.cpp:781` 解析的是 `rd=` / `rootdev=`。写 `root=md0` 会被静默忽略。
mockfs RAM 根的正确写法：`rd=md0`。

### 9.4 mockfs RAM 根 —— 完整接线（今天打通）

XNU 的 `miscfs/mockfs`（`CONFIG_XNU_SOURCE_MOCKFS=y` + `-DMOCKFS=1` 编进内核）
提供一个最小可执行根：设备内容是一个 `N4KROOT` 容器，内含
`/sbin/launchd`（签名静态 Mach-O）和 `/probe.bin`。内核把它 exec 成 PID 1。

接线五件套（全部由 `./unixnu payload/bootimg` 自动组装）：

1. **fixture**：`scripts/build-native-user-fixture.py` 编译静态 arm64 iOS17 进程，
   ad-hoc 签名，产出 `launchd.macho` + `trustcache.bundle`（cdhash 信任模块）。
2. **根镜像**：按 `<8sII4Q` 头打包 `N4KROOT\0 + version=1 + count=2 +
   launchd@4096 + probe.bin`（约 45 KB）。
3. **payload**：`--data-ramdisk root.img --trust-cache trustcache.bundle`
   → `scratch-data.img` 进 payload FAT，ADT `/chosen/memory-map` 写入
   `RAMDisk`/`TrustCache` 16 字节占位（`devicetree.py generate`）。
4. **DarwinBoot**：扫描 ADT 占位 → 把 scratch-data.img 拷进 DarwinBoot 内存窗口 →
   回填真实地址/大小。
5. **内核**：`IOKitBSDInit` 读 memory-map RAMDisk → `mdevadd` → `/dev/md0`；
   `rd=md0` 直接命中（跳过 Apple_HFS 设备等待）；mockfs 挂根 →
   exec `/sbin/launchd` → AMFI 验签 → **PID 1**。

信任白名单：`profiles/os/ipados-17.7.11-21H461/profile.toml` 的
`trust_cache_sha256` 数组要加入 fixture bundle 的 sha256。

### 9.5 boot-progress 仪器（卡死定位必备）

`rd=md0` 之后若挂载链卡死，屏幕毫无输出。开了 `CONFIG_XNU_BOOT_PROGRESS=y`
+ archives `build-boot-progress.py` + 内核参数 `boot-progress=2` 后，**内核每过一个
阶段就在 framebuffer 上画一行**。这套 kit 在 sheng/alioth 设备仓里
（framework 不带），alioth 与 cas 同为 SM8250，kit 可直接复用，只需：

- `boot_progress_exit` 签名对齐本框架补丁（6 参→5 参，reason 参数置 NULL）；
- baseline 的 UFS/owner-root 门禁放宽（cas 没有 UFS，保留 corecrypto 检查）；
- 一个桩生成器：扫包内全部 `extern`（weak_import + 普通）声明，`llvm-nm` 对已编对象
  去重后，自动生成缺失符号的零值全局桩和 `return 0` 函数桩
  （注意：函数桩不能写匿名参数——C23 扩展会被 `-Werror` 拒）；
- 桩编译去掉 `-Wextra -Werror`。

### 9.6 上机结果（v7：cas-mac-v7-bp.img）

```
ts: SUCCEEDED - OS and diagnostic recording continues
ts pid=3 process signature accepted          ← AMFI 放行
ts pid=3 load_init_program: enter → returned
ts pid=3 1 launchd: 10000038a/0              ← PID 1 = 我们的 launchd
process exit: total=2 nonzero=0 status=0 reason=0 dropped=0
UFS TIME verify=0 ms read=0 ms verified=0 blocks (0 MiB) requests=0   ← 桩如实报零
```

链路：`rd=md0` → DarwinBoot 回填 RAMDisk → XNU `mdevadd` → mockfs 挂根 →
exec `/sbin/launchd` → AMFI 验签 → **PID 1 运行并正常退出**。
镜像 sha256 `8333135a…`（boot-progress 内核 `60b8d784…`）。

### 9.7 现状与下一步

§4.1 的阶段表更新：1–12 ✅ → DarwinBoot ✅ → XNU ✅ → mockfs RAM 根 ✅ → **PID 1 ✅**。
通往桌面的剩余路径 = PORTING-PLAN 阶段 2–4：**UFS 驱动**（读 userdata 里的
OS/SystemOS 窗口）→ 真实 iOS 根 + 真 launchd → legacy display/GPU → SpringBoard。

---

## 10. Mac 构建时间账

| 阶段 | 耗时（Mac M 系列原生） |
|---|---|
| 固件（EDK2，含修复重放） | 约 20 秒 |
| kernel（40+ 补丁 + prelink，IPSW 流式 range 拉取） | 约 1.5 分钟 |
| payload | 数秒 |
| bootimg 打包（含 shim 编译与回读校验） | 数秒 |
| fixture + 根镜像 | 数秒 |

**注意**：Windows 那套 `wsl-build.sh` 的 kernel 步骤产物（Linux ELF 工具链）与 Mac
不通用，两条机器各自全量构建；payload/bootimg 层产物可以互相校验。

---

*§9 由 Mac 实机调试过程整理，命令在 Apple Silicon macOS 27.2 + Xcode 26/27.2 上验证过。*

---

## 11. 2026-11-10 晚：UFS 真根第一日——真机 PID 1 确认、三层根因与 userdata 刷写

### 11.1 成果一览

| 里程碑 | 状态 |
|---|---|
| boot-progress 仪器版（v7）确认真机 **PID 1**：`process signature accepted` → `load_init_program` → `1 launchd` → `process exit: total=2 nonzero=0` | ✅ |
| UFS 组件链四件套接入（feeds / board.toml / defconfig / cas.dts） | ✅（但见 11.3——驱动当晚才真正进内核） |
| `userdata-root.img` 构建并刷入真机（SHNGROOT 布局，OS+SystemOS 窗口哈希与 policy 闭环） | ✅ |
| verified-root host 工具链（openssl@3 + RSA3072 owner 密钥 + verified-root.dylib） | ✅ |
| 两个 donor 卷 dmg 下载并字节级验证（76040 OS / 76034 SystemOS，sha 与脚本硬编码期望一致） | ✅ |
| 完整 IPSW（6.9 GB）断点续传落盘 | ✅ |

### 11.2 umi→cas 移植路线（社区调研）

`reAAAq/UniXNU-umi`（小米 10，同 SM8250、同 iPad7,1-21H461 donor）已跑到 **iPadOS 17.7.11 Hello/Setup Assistant**（attempt 80）：UFS root + persistent Data + CPU 显示（ABL scanout）+ ST FTS 触摸全部工作。cas 的到桌面路线定型为**搬 umi/alioth 的组件**（调参级，非写驱动级）。UFS 驱动本体 = 公开仓 `UniXNU/ShengUfs` 的 SM8250 fork `FFT-infra/NativeUfs-alioth`（按 `qcom,sm8250-ufshc` compatible 扫 DT 匹配，takeover 依赖 ABL 留下的活控制器，轮询不占中断）。

### 11.3 💣 三层根因：UFS 驱动从未进过任何 cas 内核

v9/v10 两轮上机面板全零（`published=0 probe=0 match=0`）+ `Failed to mount root device @IOKitBSDInit.cpp:984`。逐层排查揪出三个叠加缺陷，**每个都足以单独让驱动静默消失**：

1. **defconfig 解析器 last-wins**：`# CONFIG_X is not set` 行是**活的**（框架 config.py 把它解析成 False）。旧默认关的 not-set 标记残留在新加的 `=y` 行**后面**，每次解析把 `XNU_NATIVE_UFS` / `XNU_NATIVE_VIRTIO_BLOCK` 覆盖回 0 → baseline 的 force_load 块从未带上 UFS。**教训：`is not set` 标记绝不能与同名 `=y` 共存，且必须写在它前面。**
2. **storage-bindings 覆盖驱动归档**：`component:ShengUfs` 产出含 `NativeUfs.o`（605 KB）的 `libShengUfs.a` 后，bindings 脚本用 `shutil.copyfile(bindings归档, target)` 把它覆盖成 2.7 KB 纯绑定壳。修复：target 已存在时改用 `ar rcs target original-bindings.o` **追加**绑定对象，不覆盖。
3. **boot-progress 桩与驱动真符号冲突**：桩生成器 nm 只扫 boot-progress 对象；驱动真正链接后会撞重复定义。修复：nm 追加扫描 `build/sheng-ufs/libShengUfs.a` + `build/virtio-mmio-block/libVirtioMmioBlock.a`，驱动已提供的符号不再生成桩。

验证手段（值得抄走）：`llvm-nm kernel.macho | grep native_ufs` —— **桩定义在 .bss（`S`），驱动的初始化定义在 data（`D`）**；看见 `S` 就是桩在撒谎。再配 `strings kernel.macho | grep 'NativeUfs:'` 确认驱动代码真的在内。

另：baseline 整个 force_load 块的守卫是 `XNU_SOURCE_CORECRYPTO14=1 && BOOTSTRAP=0`，UFS/boot-progress/verified-root 的归档全在里面——defconfig 缺 CORECRYPTO14 时这些开关全都静默失效。

### 11.4 fastboot 大文件刷写坑（8.7 GB userdata）

| 现象 | 根因 | 对策 |
|---|---|---|
| `flash userdata` >2.3 GB 即静默失败（600 MB/1.5 GB 正常） | fastboot 37 host 端自动 sparse 转换在大文件上溢出 | 自写**分段 sparse**：14 段、每段 ~664 MB raw（`0xCAC1`）+ 前段 FILL(0)（`0xCAC2`+4 字节 0 填充值，**不是** `0xCAC3` dont-care——dont-care 留 unmapped 块，UFS 读会报错） |
| 奇偶交替 `Last flash failed: Volume Corrupt` | 小米 bootloader 在两次 flash 间检查 f2fs 卷状态，raw 数据破坏卷头即拒绝 | 状态机规律 = 成功→失败→成功；**每段重试至多 2 次必成**（失败那次数据未写入，不损坏分区） |

userdata 镜像由框架原生 `./unixnu userdata` 一步构建（SHNGROOT：OS 窗口 6,077,580,800 B @ LBA 0x100000 + SystemOS 3,303,014,400 B @ 0x16a600000），[userdata] 窗口大小要 ≥ 镜像 8.74 GiB（原分区尾会产生 240 GB 稀疏文件）。

### 11.5 下一步（原料全部就绪）

1. **verified-root bundle 归档**：OS-only 模式的 probe 归档不含 `_verified_root_policies/_verified_root_policy_count`（只在 `--include-systemos` bundle 分支生成）。两份 policy（os 1,483,785 块 / systemos 806,400 块）已验证，跑 `build-verified-root-probe.py --component os --include-systemos` 换 bundle 归档即可。
2. 补齐 `IOMedia::metaClass`（IOStorageFamily 侧）与 `_native_verified_block_root_authorized`（offline-owner 组件侧）的链接来源。
3. 内核带驱动重编 → `fastboot boot` → UFS 真根 verified-block 逐块验证 → 发布 `ufsroot0` → 真 launchd（OS trust cache 打包：static-trustcache.bin 已解出，sha 与框架期望一致）。

---

*§11 由 2026-11-10 晚 Mac 实机调试整理；fastboot 分段 sparse 生成器与设备状态机结论在小米 10 Ultra（cas）上实测有效。*

---

## 12. 2026-11-10 夜（Windows 端）：两个链接符号的来源定位

Mac 那边 §11.5 留下的三个卡点，Windows 端把源码全拉下来逐个定位清楚了，
解法与命令顺序见 **`UFS-ROOT-DAY2.md`**。结论：

| 缺失符号 | 定义处 | 链接来源 |
|---|---|---|
| `native_verified_block_root_authorized` | `VirtioMmioBlock/NativeVirtioBlock.cpp:317`（`-DNATIVE_VERIFIED_BLOCK=1`） | `build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle` → `build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a`，由 `CONFIG_XNU_NATIVE_VIRTIO_BLOCK_ARCHIVE` force_load |
| `IOMedia::gMetaClass` | **不定义**——IOStorageFamily 是 prelinked kext | `.globl/.set` 绝对地址别名（样板 `scripts/data/native-aes-original-bindings.S`）**追加**进驱动归档 |

`build-virtio-mmio-bindings.py` 只覆盖 `IOBlockStorageDevice` 家族，**不含 `IOMedia`**——
这就是 `IOMedia::metaClass` 缺失的原因。新工具
`tools/fill-missing-original-bindings.py` 用 `native_prelink.exports()` 从 prelinked
内核查地址，把 IOMedia 等缺失绑定补上并 `ar rcs` 追加（不覆盖）。

verified-root bundle 归档（§11.5-1）只是参数问题：`--include-systemos` 才产
`_verified_root_policies` / `_verified_root_policy_count`，alioth 的
`CONFIG_XNU_VERIFIED_ROOT_ARCHIVE` 指的就是 `verified-root-os-xnu-bundle/`。

另从 umi（`reAAAq/UniXNU-umi`，同 SM8250，已到 iPadOS 17.7.11 Hello/Setup Assistant）确认：
**owner-root 必须同时开 `XNU_NATIVE_AES` + `XNU_NATIVE_VIRTIO_BLOCK`**（`build_umi_xnu.py:141`）。

参考源码已按 `components.toml` 固定 revision 拉到 `D:////unixnu////src////ref////`：
`VirtioMmioBlock@4e1013e`、`VerifiedRoot@974f043`、`RamBlock@93dd9af`、`XnuPrelink`、`umi`。

---
