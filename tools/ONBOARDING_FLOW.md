# OpenAI 注册后引导页流程文档

## 概述

本文档描述了 OpenAI 账号注册成功后的完整引导流程，共 11 个步骤。

---

## 步骤 1-2：处理初始弹窗

**目标：** 关闭注册成功后出现的弹窗

**元素：**
```html
<div class="flex items-center justify-center">跳过</div>
```

**操作：**
- 查找并点击"跳过"或"Skip"按钮
- 最多尝试 2 次（可能有多个弹窗）

---

## 步骤 3：跳过导览

**目标：** 跳过新手引导教程

**元素：**
```html
<div class="flex items-center justify-center">跳过导览</div>
```

**操作：**
- 等待 2 秒页面加载
- 点击"跳过导览"或"Skip tour"

---

## 步骤 4：点击继续

**目标：** 进入下一步

**元素：**
```html
<button class="btn relative btn-primary btn-large w-full">
  <div class="flex items-center justify-center">继续</div>
</button>
```

**操作：**
- 通过 `btn-primary` 类或文本"继续"/"Continue"定位
- 点击按钮

---

## 步骤 5：选择免费赠品

**目标：** 选择赠品选项

**元素：**
```html
<button type="button" class="flex items-center gap-1 bg-transparent...">
  <svg>...</svg>免费赠品
</button>
```

**操作：**
- 点击"免费赠品"或"Free gift"
- 随机延迟 2-3 秒

---

## 步骤 6：选择 Business 套餐

**目标：** 选择 Business 订阅计划

**元素：**
```html
<button class="btn relative btn-purple btn-large w-full" 
        data-testid="select-plan-button-teams-create">
  <div class="flex items-center justify-center">获取 Business</div>
</button>
```

**操作：**
- 优先通过 `data-testid` 定位（最精确）
- 备选：`btn-purple` 类或文本匹配
- 随机延迟 2-3 秒

---

## 步骤 7：继续结算

**目标：** 进入支付页面

**元素：**
```html
<button class="btn relative btn-green mt-8 w-full rounded-xl">
  <div class="flex items-center justify-center">继续结算</div>
</button>
```

**操作：**
- 通过 `btn-green` 类或文本"继续结算"定位
- 点击后等待跳转到 `pay.openai.com`
- 随机延迟 2-3 秒

---

## 步骤 8：填写支付表单

**目标：** 在 pay.openai.com 支付页面填写信息

**前置条件：** 等待 URL 包含 `pay.openai.com`（最多 30 秒）

### 表单字段

| 序号 | 字段 | ID | 类型 | 测试值 |
|------|------|-----|------|--------|
| 1 | 邮箱 | `#email` | input | test@example.com |
| 2 | 卡号 | `#cardNumber` | input | 4242424242424242 |
| 3 | 有效期 | `#cardExpiry` | input | 12/28 |
| 4 | CVC | `#cardCvc` | input | 123 |
| 5 | 持卡人 | `#billingName` | input | Test User |
| 6 | 国家 | `#billingCountry` | select | US |
| 7 | 地址 | `#billingAddressLine1` | input | 123 Test Street |
| 8 | 城市 | `#billingLocality` | input | New York |
| 9 | 邮编 | `#billingPostalCode` | input | 10001 |
| 10 | 州 | `#billingAdministrativeArea` | select | NY |
| 11 | 许可协议 | `input[type="checkbox"]` | checkbox | 勾选 |

### 自动填写能力

**全部可以自动填写 ✅**

- 所有 input 字段通过 ID 精确定位
- select 下拉框通过 `.select(value)` 选择
- checkbox 通过 `.click()` 勾选

---

## 步骤 9：付款成功后点击继续

**目标：** 付款完成后进入下一步

**触发条件：** URL 包含 `chatgpt.com/payments/success-team`

**元素：**
```html
<button class="btn relative btn-primary btn-large w-full">
  <div class="flex items-center justify-center">继续</div>
</button>
```

**操作：**
- 等待付款成功页面（最多 120 秒）
- 点击"继续"按钮

---

## 步骤 10：跳过团队名称

**目标：** 跳过团队名称输入，使用默认值

**元素：**
```html
<button class="btn relative btn-primary btn-large w-full">
  <div class="flex items-center justify-center">继续</div>
</button>
```

**操作：**
- 不输入团队名称
- 直接点击"继续"按钮

---

## 步骤 11：获取 Session 数据

**目标：** 获取登录凭证用于后续 API 调用

**操作：**
1. 访问 `https://chatgpt.com/api/auth/session`
2. 解析返回的 JSON 数据
3. 保存到 `register_results.txt`

**返回数据示例：**
```json
{
  "user": {
    "id": "user-xxx",
    "email": "xxx@example.com",
    "name": "Test User"
  },
  "accessToken": "eyJhbGciOiJSUzI1NiIs...",
  "expires": "2025-02-14T..."
}
```

---

## 完整流程图

```
注册成功 (chatgpt.com)
    │
    ▼
[1-2] 关闭弹窗 (跳过)
    │
    ▼
[3] 跳过导览
    │
    ▼
[4] 点击继续
    │
    ▼
[5] 选择免费赠品 ──────► 延迟 2-3s
    │
    ▼
[6] 选择 Business ─────► 延迟 2-3s
    │
    ▼
[7] 继续结算 ──────────► 延迟 2-3s
    │
    ▼
[8] 填写支付表单 (pay.openai.com)
    │   ├─ 8.1 邮箱
    │   ├─ 8.2 银行卡号 (iframe)
    │   ├─ 8.3 有效期
    │   ├─ 8.4 CVC
    │   ├─ 8.5 持卡人姓名
    │   ├─ 8.6 地址
    │   └─ 8.7 勾选协议
    │
    ▼
[9] 付款成功点击继续 (等待最多 120s)
    │   URL: chatgpt.com/payments/success-team
    │
    ▼
[10] 跳过团队名称
    │
    ▼
[11] 获取 session 数据
    │   URL: chatgpt.com/api/auth/session
    │
    ▼
保存到 register_results.txt
    │
    ▼
等待人工检查 (Ctrl+C 关闭)
```

---

## 输出文件格式

**文件：** `tools/register_results.txt`

**格式：**
```
邮箱 | 密码 | 时间 | 状态 | session数据(JSON)
```

**示例：**
```
test@example.com | Password123 | 2025-01-15 17:30:00 | 成功 | {"user":{"id":"user-xxx"},"accessToken":"xxx"...}
test2@example.com | Password123 | 2025-01-15 17:35:00 | 失败
```
