# 进度与续接（2026-11-11 08:45）

> 昨天 02:20 那版已作废（Windows 时代、根挂载未通）。这是当前真实状态。

## 一句话状态

**真机 PID 1 已达成（mockfs RAM 根）；UFS 真根链路已接完，内核首次链接 UFS 驱动时卡在 4 个符号。**

```
空目录 → 自编 clang → XNU 内核 → 固件 → payload → boot.img
→ UniXNU 菜单 ✅ → DarwinBoot ✅ → ExitBootServices ✅
→ XNU 早期初始化 ✅ → AMFI ✅ → bsd_init ✅
→ mockfs RAM 根 + 签名 launchd → PID 1 ✅（v7 实证）
→ UFS 驱动进内核 → 4 个链接符号待补 ⚠️   ← 当前
```

## 当前唯一阻塞：4 个链接符号

修完 defconfig / bindings / 桩生成器三层根因后（README §11.3），
`libShengUfs.a` 首次被 force_load，链接器点名：

| 符号 | 归属 | 解法 |
|---|---|---|
| `_verified_root_policies`、`_verified_root_policy_count` | probe 归档只有 `--include-systemos` bundle 分支才 emit | `build-verified-root-probe.py --component os --include-systemos` → `build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a`，软链到 `build/verified-root-xnu/libVerifiedRootProbe.a` |
| `IOMedia::metaClass` | IOStorageFamily 侧（驱动发布 `ufsroot0` BSD 名要 IOMedia） | 查 IOStorageFamily 组件/预链接来源 |
| `_native_verified_block_root_authorized` | offline-owner 补丁只 extern 声明，定义在 verified-block 驱动侧 | 查 UFS 组件的 verified-block 产物谁提供 |

## 已经不用再做的事

| 项 | 状态 |
|---|---|
| Mac 全链构建（firmware / kernel / payload / bootimg） | ✅ 稳定，20 秒 / 1.5 分钟 |
| `PcdDebugPropertyMask 0x0F`、`rd=` 参数名、boot-progress 桩工程 | ✅ 已修（README §9） |
| mockfs RAM 根五件套（fixture + trustcache + N4KROOT + rd=md0） | ✅ 真机 PID 1 |
| UFS 组件接线（feeds / board.toml / cas.dts ufshc 节点 / bindings 追加） | ✅ 归档 612 KB 含 NativeUfs.o |
| userdata-root.img 构建 + 刷入（14 段 sparse + 重试） | ✅ 已落盘真机 |
| verified-root host 工具链 + 两份 policy（1,483,785 / 806,400 块） | ✅ |
| donor 双 dmg + 完整 IPSW | ✅ 字节级校验通过 |

## 产物位置（Mac）

```
unixnu-build/UniXNU/out/cas/ipados-17.7.11-21H461/cas/
├── kernel/kernel.macho                     # 内核（当前无驱动版，需重编）
├── payload/payload.img                     # payload
├── userdata/userdata-root.img              # 8.74 GiB iOS 真根镜像（已刷机）
├── tree/build/sheng-ufs/libShengUfs.a      # 612 KB，NativeUfs.o + bindings
├── tree/build/verified-root-os-xnu/        # OS-only probe（缺 policies 表）
└── logs/kernel.log                         # 链接错误全在这
unixnu-build/UniXNU/out/ipados/21H461/
├── 044-76040-038.dmg（OS 4.8G）、044-76034-038.dmg（SystemOS 3.0G）
└── *.trustcache / *.root_hash
unixnu-build/UniXNU/refer/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw   # 6.9 GB
```

## 上机流程（Mac）

```bash
cd unixnu-build
tools/platform-tools/fastboot devices        # 先探测，在就直接刷
tools/platform-tools/fastboot boot UniXNU/out/cas-mac-vXX.img
```

长按电源 12 秒回 Android。

## 四条教训（新）

1. **defconfig 的 `# CONFIG_X is not set` 是活的**，且后写覆盖 `=y`——绝不能与同名 `=y` 共存（README §11.3）。
2. **bindings 脚本只能追加，不能覆盖组件归档**——否则驱动静默消失，面板计数器全是桩零值。
3. **验证驱动在不在内核**：`llvm-nm kernel.macho | grep native_ufs`——`S`（.bss）= 桩，`D` = 驱动真定义。
4. **fastboot 37 刷 >2.3 GB 会静默失败**：分段 sparse（FILL(0) 前导）+ 每段重试 2 次（README §11.4）。

## 遗留（不影响主线）

- DXE 冷启动偶发卡顿（约 50% 第一次），重刷可绕过。
- 显示 / 触摸驱动未做（umi 已有现成组件可搬）。
