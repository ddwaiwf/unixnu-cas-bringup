# Mac 侧续接清单（2026-11-10 夜）

Windows 端已经把所有分析、配置、脚本、工具链都备好了。
**Mac 是构建机**，下面这条链在 Mac 上一次跑通即可出可上机镜像。

## 前置：应用今晚的三处修改

```bash
cd ~/unixnu/src/UniXNU                 # 或你的实际路径

# 1. IOMedia 绑定修复（框架脚本）
git apply ~/unixnu/repo-cas-bringup/patches/storage-bindings-cover-iomedia.patch
#    兜底脚本（导出表没有 IOMedia 时才需要）
cp ~/unixnu/repo-cas-bringup/tools/audit-iomedia-metaclass.py scripts/

# 2. 设备仓配置（defconfig / board.toml 已在 Windows 侧改好，同步过去即可）
#    cas-full/configs/cas_defconfig
#    cas-full/board.toml

# 3. 驱动源码（Windows 侧已放进 WSL 树；Mac 树需要同样补）
cp -r ~/unixnu/src/ref/VirtioMmioBlock   tree/drivers/VirtioMmioBlock
cp -r ~/unixnu/src/ref/NativeUfs-alioth  tree/drivers/ShengUfs
cp -r ~/unixnu/src/ref/VerifiedRoot       tree/kits/VerifiedRoot
cp ~/unixnu/src/alioth/scripts/build-boot-progress.py  tree/scripts/
cp ~/unixnu/src/alioth/scripts/build-link-stubs.py     tree/scripts/
```

## 一条命令构建

```bash
cd tree && bash build-ufs-root.sh
```

它依次做：
1. `build-verified-root-probe.py --component os --include-systemos` → bundle 归档
2. `build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle`
3. `build-arm-driver.py NativeUfs`
4. `audit-iomedia-metaclass.py` → 把 IOMedia 元类地址写进 `interface-audit.json`
5. `build-virtio-mmio-bindings.py` → 生成绑定归档
6. `./unixnu kernel`

每步都有 `llvm-nm` 门禁：符号必须是 `T`（真身）不能是 `S`（桩）。

## Windows 端已验证的链接配方（Mac 侧同样适用）

树里记着内核链接要的一切，**不需要重编 XNU**：

| 文件 | 内容 |
|---|---|
| `mach.development.bcm2837.link/mach.development.bcm2837.linkarguments` | 全部 ld 参数 |
| `mach.development.bcm2837.link/mach.development.bcm2837.linkarchives` | 归档与 force_load 列表 |
| `link.filelist` | 1006 个目标文件 |
| `all-alias.exp` | 85 条别名 |

**三个不明显的点**（Windows 端实测踩出来的）：

1. `link.filelist` **漏了 4 个家族对象**，原链接是直接传的：
   `version.o`（`osversion`/`ostype`）、`lastkernelconstructor.o`（`last_kernel_symbol`）、
   `lastkerneldataconst.o`、`nonlto.o`
2. `all-alias.exp` 的别名（`_vm_map_create` ← `_vm_map_create_external` 等）
   靠 ld64 的 `-alias_list` 生成。**如果用 lld，必须改用汇编 `.set`**：
   ```asm
   .globl _vm_map_create
   .set  _vm_map_create, _vm_map_create_external
   ```
   实测别名与真身地址相同（现有 kernel.macho 里 `_kalloc_type_impl` 与
   `_kalloc_type_impl_external` 同址）。
3. `-sectcreate` 的源 `/dev/null` 要换成真实空文件。

**Mac/WSL 有真 ld64（cctools-port），直接用原来的链接命令即可**，上面三点本来就没问题。

## 还差的最后一步（必须在 Mac/WSL 做）

`build-storage-bindings.py` 需要 **prelinked candidate**（`build/xnu-prelink/
native-apfs-load-positive.macho`）和 **live metadata**
（`build/darwinboot-storage-native-metadata/gdb-stop.json`）。
后者要从**真机运行中的内核**抓，所以链路是：

```
编内核 → 生成 candidate → 刷机 boot → GDB 抓 metadata → 审计 → 绑定 → 重链 → 再刷
```

第一轮可以先跳过绑定：只带 UFS 驱动（不 force_load verified-root）上机，
用 boot-progress 面板看 `published/probe/match` 是否非零——
**全零说明驱动没进内核**（README §11.3 的三层根因），非零说明驱动活了，
再补绑定走鉴权路径。

## 刷机

```bash
fastboot boot out/cas-*.img          # 先试 boot，不动 userdata
fastboot flash userdata userdata-root.img   # 大文件要用分段 sparse，见 README §11.4
```

手机现在就在 fastboot 模式（`805d91ce`），随时可刷。
