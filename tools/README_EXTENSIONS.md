# Auto-Provisioner 扩展工具集说明

本文档记录了 `tools/` 目录下新增的扩展脚本及其功能。这些脚本基于上游核心代码（`browser_automation.py`, `email_service.py` 等）构建，提供了批量注册、流程引导等增强功能。

## 目录结构

```
tools/
├── batch_register.py       # 批量注册主入口
├── onboarding_flow.py      # 注册后的引导页流程处理（点击继续、选择套餐等）
├── README_EXTENSIONS.md    # 本文档
└── register_results.txt    # (自动生成) 批量注册结果日志
```

## 核心原则

1.  **不侵入上游**：所有扩展功能尽量封装在 `tools/` 目录下，不直接修改项目根目录的核心文件（除非是为了修复 Bug 或适配环境）。
2.  **依赖上游**：扩展脚本调用根目录的 `browser_automation.py` 和 `email_service.py` 来复用核心能力（如浏览器启动、邮件获取）。
3.  **独立配置**：部分扩展功能可能有独立的配置参数，但尽量复用 `config.toml`。

## 工具详解

### 1. 批量注册工具 (`batch_register.py`)

**功能**：自动化批量创建邮箱并注册 OpenAI 账号。

**用法**：
```bash
# 仅创建 4 个邮箱（不注册）
python tools/batch_register.py create 4

# 创建 4 个邮箱并逐个注册（推荐）
python tools/batch_register.py register 4

# 创建 4 个邮箱并注册，启动前延迟 10 秒
python tools/batch_register.py register 4 10
```

**依赖**：
- `email_service.py`: 用于批量创建邮箱。
- `browser_automation.py`: 用于执行注册流程。
- `tools/onboarding_flow.py`: 用于处理注册成功后的引导步骤。

**特点**：
- 注册完成后**保持浏览器打开**，等待人工检查或引导流程完成。
- 自动记录成功/失败的账号到 `tools/register_results.txt`。
- 支持 Windows/Linux (需配合 Headless 配置)。

### 2. 引导流程处理 (`onboarding_flow.py`)

**功能**：处理 OpenAI 注册成功后的一系列弹窗和表单。

**涵盖步骤**：
1.  **初始弹窗**：自动点击 "Skip" / "跳过"。
2.  **导览**：自动点击 "Skip tour" / "跳过导览"。
3.  **遮罩层继续**：**重点优化**。使用 12 次重试机制和精确选择器，解决全屏遮罩下“继续”按钮难以点击的问题。
4.  **免费赠品**：自动选择 "Free gift"。
5.  **套餐选择**：自动选择 "Business" 套餐。
6.  **结算表单**：
    *   自动填写测试卡信息（卡号、CVC、有效期）。
    *   **跳过国家选择**（使用默认值，避免下拉框选择失败）。
    *   自动填写地址信息。

**调用方式**：
通常由 `batch_register.py` 自动调用，无需手动运行。
也可用于测试：
```bash
# 打开浏览器测试引导流程 (需要手动登录到对应页面)
python tools/onboarding_flow.py test
```

## 注意事项

*   **浏览器环境**：在 Linux 服务器上运行 `batch_register.py` 时，请确保 `config.toml` 中设置了 `headless = true`（上游代码已支持），或配置好 X11 转发。
*   **网络问题**：如果遇到 `Handshake status 404` 或 CloudMail 连接超时，请检查 `config.toml` 中的 `[proxy]` 设置，或在系统环境变量中正确配置/排除代理。
*   **代码更新**：拉取上游更新时，请注意检查 `browser_automation.py` 的接口变化。如果注册函数签名变更，需要同步修改 `batch_register.py`。

## 更新日志

*   **2025-01-16**:
    *   完善 `onboarding_flow.py`，增加对全屏遮罩按钮的点击重试（12次）。
    *   `batch_register.py` 移除 Linux 专用补丁（上游已原生支持无头模式）。
    *   文档初始化。
