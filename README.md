# chenmojino 的个人主页

一个运行在自己电脑上的静态个人主页，通过 Cloudflare Tunnel 暴露到公网。

## 访问

- 🌐 <https://chenmojino.xyz>
- 💻 本地：<http://127.0.0.1>

## 特性

- 深色「墨绿 + 琥珀金」主题，玻璃拟态卡片
- 访客统计：总访问次数、访客 IP、地区（国家/省/市）、访问时间
- 访问记录表格：可滚动查看历史每一次访问
- 自己的访问不计数（本机 + 校园网 IP 段排除）

## 文件结构

| 文件 | 说明 |
| --- | --- |
| `index.html` | 页面本体（HTML + CSS + JS，单文件） |
| `web.config` | IIS 配置（ASP 错误详情，可选） |
| `counter_server.py` | 访问计数器服务（Python + SQLite，监听 127.0.0.1:8081） |

## 部署方式

1. 把 `index.html` 放进 IIS 站点根目录（如 `C:\inetpub\wwwroot`）
2. 运行计数器服务：

   ```bash
   python counter_server.py
   ```

3. 用 Cloudflare Tunnel 暴露（需要 `cloudflared`）：

   ```yaml
   tunnel: chenmojino
   credentials-file: <你的凭据文件>
   ingress:
     - hostname: chenmojino.xyz
       path: /count
       service: http://localhost:8081
     - hostname: chenmojino.xyz
       path: /visits
       service: http://localhost:8081
     - hostname: chenmojino.xyz
       service: http://localhost:80
     - service: http_status:404
   ```

## 数据存储

SQLite 数据库 `visitors.db`：

- `counter` 表：总访问数
- `visits` 表：每次访问的 时间 / IP / 国家 / 省 / 市 / ISP / UA

## License

Personal project, feel free to reference.
