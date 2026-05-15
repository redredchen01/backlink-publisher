# Medium OAuth 授权设置

## 快速开始

如果你的 Medium Integration Token 已失效，可以使用 OAuth 授权来重新获取 Medium API 访问权限。

### 步骤 1: 创建 Medium 应用

1. 访问 [https://medium.com/me/apps](https://medium.com/me/apps)
2. 点击「Create a new application」
3. 填写应用信息：
   - **Application name**: 比如「Backlink Publisher」
   - **Application website**: 可以填你的网站 URL
   - **Redirect URLs**: 输入 `http://localhost:5000/settings/medium/oauth-callback`
4. 点击「Create application」
5. 复制以下信息：
   - **Application ID** (客户端 ID)
   - **Application Secret** (客户端密钥)

### 步骤 2: 在 Settings 中授权

1. 打开 Web UI 的 Settings 页面
2. 找到「Medium 授权」卡片
3. 在表单中填入：
   - **Client ID**: 上面复制的 Application ID
   - **Client Secret**: 上面复制的 Application Secret
4. 点击「通过 Medium 授权」按钮
5. 会跳转到 Medium 授权页面，点击授权
6. 授权成功后会自动返回 Settings 页面

### 步骤 3: 验证

授权成功后，你会看到「✅ OAuth 已授权」的提示。

## 常见问题

### Q: OAuth 和 Integration Token 有什么区别？

- **OAuth**: 现在推荐的认证方式，通过浏览器授权
- **Integration Token**: 已停用但仍可用的认证方式（已失效）

### Q: 如果我既有 OAuth 又有 Integration Token？

系统会优先使用 OAuth Token。如果 OAuth 失效，会自动尝试使用 Integration Token。

### Q: 授权失败怎么办？

检查以下几点：
1. Client ID 和 Client Secret 是否正确？
2. Redirect URI 是否填对了？（应该是 `http://localhost:5000/settings/medium/oauth-callback`）
3. 本地服务是否在 5000 端口运行？

### Q: 如何清除 OAuth 授权？

在 Settings 页面，点击「清除 OAuth 授权」按钮即可。

## 文件位置

OAuth Token 保存在：
```
~/.config/backlink-publisher/medium-token.json
```

此文件包含敏感信息，权限限制为 600 (仅当前用户可读写)。
