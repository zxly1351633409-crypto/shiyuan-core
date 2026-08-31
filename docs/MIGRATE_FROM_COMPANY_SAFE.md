# 从公司安全模式迁移到个人 Core

适用于误装 shiyuan-company-safe、但实际希望使用个人 Core 的电脑。

## 安全边界

- 公司安全模式的本地状态不会自动上传个人 Core。
- 迁移过程先归档旧组件和状态，不直接删除。
- 如果电脑受公司制度约束，不得绕过制度迁移到个人 Core。

## 迁移

在仓库根目录运行：

    .\install.ps1 -AssistantName "你的助手名称" -ReplaceCompanyMode

安装器会先调用公司版卸载脚本，把旧组件和本地状态移动到带时间戳的停用归档，
再安装个人 Core。

## 验收

1. 用户目录 .codex/config.toml 中不再存在 SHIYUAN_COMPANY_SAFE。
2. 配置中只存在一组 SHIYUAN_PERSONAL_CORE。
3. 用户目录 .shiyuan/INSTALL_REPORT.md 显示“个人 Core”。
4. 重启应用并新建会话后，不再显示“公司安全模式”。

公司端原有内容不会自动进入个人记忆。只有确认合规后，才可以人工整理脱敏摘要。
