# 设备仓改动快照（2026-11-11）

`UniXNU-cas/` 是本地设备仓 `ht94911/UniXNU-cas.git` 在提交 `ae147fb` 相对上游
`5cba141` 的全部改动文件（33 个）。上游仓库属主不是本账号，`git push` 返回
`Permission to ht94911/UniXNU-cas.git denied to ddwaiwf (403)`，因此以快照形式随
文档仓固化。要复现构建，把这些文件覆盖到设备仓对应路径即可。

内容：`board.toml`（root_match_volume_uuid、kernel_args、archives）、
`configs/cas_defconfig`、`dts/cas.dts`、`feeds.toml`、
`assets/static-trustcache.bin`（sha `7416ca4f…`，从 v21 payload FAT 取回）、
`package/` 下的 boot-progress / ShengPlatformPower 组件，以及 `scripts/` 下的
UFS 补丁脚本（bounce fix、window hash probe、verify-all probe、sparse-region、
storage bindings 等）。

README §14.4 有完整的一键构建与刷机命令。
