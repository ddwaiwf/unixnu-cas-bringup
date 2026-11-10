# UFS 真根 · 第二日清单（§11.5 三个卡点的解法）

> 2026-11-10 夜，Windows 端分析产出。Mac 是构建机，本文件里的命令在 **Mac** 上跑。
> 上游状态：mockfs RAM 根 + **真机 PID 1** 已确认（`process exit: total=2 nonzero=0`）。
> 目标：UFS 真根 → 发布 `ufsroot0` → 真 launchd。

---

## 0. 一句话结论

两个「链接来源」缺失，来源其实都是已知组件，只是没被 force_load 进内核：

| 缺失符号 | 谁定义 | 怎么进内核 |
|---|---|---|
| `native_verified_block_root_authorized` | `VirtioMmioBlock/NativeVirtioBlock.cpp:317`（`-DNATIVE_VERIFIED_BLOCK=1`） | `build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle` → `CONFIG_XNU_NATIVE_VIRTIO_BLOCK_ARCHIVE` force_load |
| `IOMedia::gMetaClass`（`__ZN6IOMedia10gMetaClassE`） | **不定义**——IOStorageFamily 是 prelinked kext | 存储绑定对象（`.globl/.set` 绝对地址别名）**追加**进驱动归档 |

`verified-root` bundle 归档（§11.5-1）只是多加一个 `--include-systemos` 参数的事，命令在 §1。

---

## 1. §11.5-1：verified-root bundle 归档

OS-only 模式（`--component os`）不产 `_verified_root_policies` / `_verified_root_policy_count`，
这两个符号**只在 `--include-systemos` 的 bundle 分支生成**（`build-verified-root-probe.py:106-118`）。
alioth 就是这么接的：`CONFIG_XNU_VERIFIED_ROOT_ARCHIVE=.../verified-root-os-xnu-bundle/libVerifiedRootProbe.a`。

```bash
# 前置（README §11.1 已确认就绪）：
#   build/verified-root-os/verification.json        ← 1,483,785 块 OS 策略
#   build/verified-root-systemos/verification.json  ←   806,400 块 SystemOS 策略
#   build/verified-root-host/keys/owner.pem         ← RSA3072 owner 密钥
#   .cache/firmware/target-volumes/044-76034-038.dmg
python3 scripts/build-verified-root-probe.py --component os --include-systemos
# → build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a
#   含 _verified_root_manifest / _verified_root_key / _verified_root_samples
#   含 _verified_root_systemos_manifest
#   含 _verified_root_policies (2 项) / _verified_root_policy_count
```

**验收**：
```bash
llvm-nm build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a | grep -E "_verified_root_policies|_verified_root_policy_count"
# 期望两个都是 D（data 定义），不是 U
```

> 注意：该脚本调 `xcrun clang` / `xcrun ar`，**只能在 Mac 跑**（Windows/WSL 要先做
> `xcrun`→`clang-24`/`llvm-ar` 的移植，参考 README §3.14 的 PMGR 桥接脚本改法）。

---

## 2. §11.5-2：两个链接来源

### 2.1 `native_verified_block_root_authorized` ← VirtioMmioBlock verified bundle

这个函数在 `drivers/VirtioMmioBlock/NativeVirtioBlock.cpp:317`：

```cpp
extern "C" int native_verified_block_root_authorized(dev_t device)
{ return verifiedVolumeQuery(device, nullptr, nullptr); }
```

它靠 weak 的 `native_ufs_block_authenticates()`（`NativeUfs.cpp:2181`，ShengUfs 导出）
去验证 UFS 窗口。alioth 上这块板子 DTS 没有 `virtio,mmio` 节点，驱动**从不 probe**，
它被链进来纯粹是为了当**只读鉴权库**。

```bash
python3 scripts/build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle
# → build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a
#   编译带 -DNATIVE_VERIFIED_BLOCK=1 -DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1
```

**验收**：
```bash
llvm-nm build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a | grep native_verified_block_root_authorized
# 期望 T/t（真实代码），**绝不能是 S/s**（那是 boot-progress 桩在撒谎）
```

### 2.2 `IOMedia::gMetaClass` ← 存储绑定对象（绝对地址别名）

IOStorageFamily 是 **prelinked**（留在 kernel collection 里），不参与内核静态链接，
所以 `IOMedia::gMetaClass`、`IOBlockStorageDevice::*` 全是链接期未定义符号。
框架的解法是 `.globl + .set` 把符号**指向 prelinked 内核里的绝对地址**
（样板：`scripts/data/native-aes-original-bindings.S`）：

```asm
.globl __ZN6IOMedia10gMetaClassE
.set  __ZN6IOMedia10gMetaClassE, 0xfffffff00xxxxxxx
```

**现成链路**：
1. `scripts/audit-storage-interface.py` → `build/storage-abi/interface-audit.json`
   （190 槽 / 176 绑定，含 `original_metaclass`、`vtable` 等地址）
2. `scripts/build-virtio-mmio-bindings.py --component NativeVirtioBlock`
   → 生成 `original-bindings.S/.o` 并 `ar rcs` **追加**进归档
   ——但它只覆盖 `IOBlockStorageDevice` 家族，**不含 `IOMedia`**。

**这正是 `IOMedia::metaClass` 缺失的原因。** 两个补法：

**(a) 首选：用本仓库的新工具补上**

```bash
# 位置：repo-cas-bringup/tools/fill-missing-original-bindings.py
# 拷到 Mac 的构建树里跑
python3 fill-missing-original-bindings.py \
    --tree /path/to/tree \
    --object build/sheng-ufs/NativeUfs.o \
    --object build/virtio-mmio-block/NativeVirtioBlock.o \
    --out-dir build/extra-storage-bindings \
    --archive build/sheng-ufs/libShengUfs.a
```

它会：扫目标文件的未定义符号 → 只挑 `IOMedia` / `IOBlockStorageDevice` /
`IOBlockStorageDriver` / `IOMediaBSDClient` 命名空间 → 用 `native_prelink.exports()`
从 prelinked 内核查地址 → 生成 `.globl/.set` → `ar rcs` **追加**（绝不覆盖）。
其余未定义符号**保持报错**（那才是真 bug）。

**(b) 兜底：手工补一行**

若只缺 `IOMedia::gMetaClass`，直接在绑定 `.S` 末尾加：

```bash
# 先从 prelinked 内核查地址（Mac，tree 根目录）
python3 - <<'PY'
import sys; sys.path.insert(0, 'kits/XnuPrelink')
import native_prelink as np
from pathlib import Path
k = Path('build/xnu-prelink/native-defaults-candidate.macho').read_bytes()
img = np.macho(k)
for addr, names in np.exports(k, img).items():
    if '__ZN6IOMedia10gMetaClassE' in names:
        print(f'.globl __ZN6IOMedia10gMetaClassE\n.set __ZN6IOMedia10gMetaClassE, {addr:#x}')
        break
PY
# 把输出追加到 original-bindings.S 后重新 ar rcs
```

**验收**：
```bash
llvm-nm build/sheng-ufs/libShengUfs.a | grep -E "IOMedia10gMetaClass|IOBlockStorageDevice10gMetaClass"
# 期望 A（absolute）或 D，绝不是 U
```

### 2.3 ⚠️ 三条已知会「静默吃掉驱动」的坑（README §11.3，重申）

1. **defconfig `# CONFIG_X is not set` 是活的**——`config.py` last-wins，
   残留在新加 `=y` 后面就把它覆盖回 0。**`is not set` 绝不能与同名 `=y` 共存。**
2. **bindings 脚本不能覆盖驱动归档**——必须 `ar rcs target bindings.o` **追加**，
   不是 `copyfile(bindings归档, target)`。
3. **桩生成器要跳过驱动已提供的符号**——否则撞重复定义。
   验证：`llvm-nm kernel.macho | grep native_ufs`，桩在 `.bss`(`S`)，真身在 data(`D`)。

另：force_load 整块的守卫是 `XNU_SOURCE_CORECRYPTO14=1 && BOOTSTRAP=0`
（`build-xnu-4k-baseline.sh:591`），CORECRYPTO14 不开则全部静默失效。

---

## 3. §11.5-3：defconfig / board.toml 接线 + 上机

### 3.1 defconfig（对照 alioth，按 cas 板级参数）

```ini
CONFIG_XNU_SOURCE_CORECRYPTO14=y          # force_load 块的总开关，必须有
CONFIG_XNU_VERIFIED_ROOT_PROBE=y
CONFIG_XNU_VERIFIED_ROOT_ARCHIVE="{tree}/build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a"
CONFIG_XNU_OWNER_ROOT_AUDIT=y
CONFIG_XNU_OWNER_ROOT_TRUST=y             # 必须与 AUDIT、NATIVE_VIRTIO_BLOCK 同开
CONFIG_XNU_NATIVE_UFS=y
CONFIG_XNU_NATIVE_VIRTIO_BLOCK=y          # 提供 native_verified_block_root_authorized
CONFIG_XNU_NATIVE_VIRTIO_BLOCK_ARCHIVE="{tree}/build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a"
CONFIG_XNU_BUILD_ARCHIVES=y
```

**写完后必查**（这是三层根因第 1 层）：
```bash
grep -n "NATIVE_UFS\|NATIVE_VIRTIO_BLOCK\|VERIFIED_ROOT\|OWNER_ROOT\|CORECRYPTO14" configs/cas_defconfig
# =y 的行后面 **绝不能** 再出现同名的 "# CONFIG_X is not set"
```

> umi 的经验：**owner-root 必须同时开 `XNU_NATIVE_AES` + `XNU_NATIVE_VIRTIO_BLOCK`**
> （`build_umi_xnu.py:141`）。cas 若走同一 profile，把 `CONFIG_XNU_NATIVE_AES=y` 也加上。

### 3.2 board.toml `[kernel] archives` 顺序（在 UFS 之后）

```toml
"component:ShengUfs",
"build-storage-bindings.py --component NativeUfs --archive libShengUfs.a --directory build/sheng-ufs",
"component:VirtioMmioBlock",
"build-storage-bindings.py --component NativeVirtioBlock --archive libVirtioMmioBlock.a --directory build/virtio-mmio-block --subset",
```

**bindings 必须排在 component 之后**（它把对象追加进 component 产出的归档）。

### 3.3 上机与验收

```bash
# 1. 重编
./unixnu kernel && ./unixnu payload && ./unixnu bootimg

# 2. 刷机前先自检内核里真的有驱动（不是桩）
llvm-nm build/xnu/kernel.macho | grep -E "native_verified_block_root_authorized|native_ufs_block_authenticates|IOMedia10gMetaClass"
strings build/xnu/kernel.macho | grep "NativeUfs:"

# 3. fastboot boot（userdata-root.img 已按 README §11.4 刷过）
fastboot boot out/cas-*.img

# 4. 面板看什么（boot-progress=2）
#    - "published=N probe=N match=N"  ← N>0 才是驱动真起来了（全 0 = 驱动没进内核）
#    - verified-block 逐块验证日志
#    - ufsroot0 发布
#    - 真 launchd（不是 mockfs 的那个）
```

**大文件刷写**（8.7 GB userdata，README §11.4）：fastboot 37 host 端自动 sparse 在
>2.3 GB 会溢出 → 用 14 段分段 sparse（每段 ~664 MB raw + 前段 `FILL(0)`，
**不要用 `0xCAC3` dont-care**，UFS 读 unmapped 块会报错）；`Volume Corrupt` 是
奇偶交替的假失败，**每段重试至多 2 次必成**。

---

## 4. 执行顺序（一张表）

| # | 动作 | 命令/文件 | 产出 |
|---|---|---|---|
| 1 | verified-root bundle | `build-verified-root-probe.py --component os --include-systemos` | `libVerifiedRootProbe.a`（含 policies） |
| 2 | VirtioMmioBlock verified bundle | `build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle` | `libVirtioMmioBlock.a`（含 root_authorized） |
| 3 | 存储绑定（补 IOMedia） | `fill-missing-original-bindings.py ... --archive libShengUfs.a` | 绑定对象追加进归档 |
| 4 | 改 defconfig | §3.1，**注意 is-not-set 位置** | — |
| 5 | 改 board.toml archives | §3.2，bindings 排 component 后 | — |
| 6 | 重编 + 自检 | `./unixnu kernel` + `llvm-nm`/`strings` | 驱动真的在内核里 |
| 7 | 上机 | `fastboot boot` + 看 boot-progress 面板 | `ufsroot0` → 真 launchd |

---

## 5. 参考资料（本次已备好，本地路径）

| 资料 | 路径 |
|---|---|
| VirtioMmioBlock 源码（含 `native_verified_block_root_authorized`） | `D:\unixnu\src\ref\VirtioMmioBlock\`（@4e1013e） |
| VerifiedRoot kit | `D:\unixnu\src\ref\VerifiedRoot\`（@974f043） |
| RamBlock 源码 | `D:\unixnu\src\ref\RamBlock\`（@93dd9af） |
| XnuPrelink（`native_prelink` 查地址） | `D:\unixnu\src\ref\XnuPrelink\` |
| NativeUfs（SM8250 fork） | `D:\unixnu\src\ref\NativeUfs-alioth\` |
| umi 社区仓（已到 Hello/Setup Assistant） | `D:\unixnu\src\ref\umi\` |
| 补绑定工具 | `repo-cas-bringup\tools\fill-missing-original-bindings.py` |

## 6. 还欠什么（诚实边界）

- **`build-storage-bindings.py` 本体不在框架里**，它来自 alioth 的某个 kit（`kits/` 是 gitlink）。
  Mac 上已经有了（README §11.3 修过它的覆盖 bug）。本清单的 `fill-missing-original-bindings.py`
  是它的**补充**，不是替代——它只补 IOMedia 等框架没覆盖的符号。
- WSL 命令、手机按键、看屏幕拍照仍需人。
- 阶段 3–4（显示/触摸/GPU）仍在后面，见 `PORTING-PLAN.md`。
