# 服务器部署

本机开发直接执行 `python run.py` 并访问 `http://localhost:8789/`。以下仅用于需要手机或远程访问的 Linux 服务器。

## 安装

示例将仓库克隆到 `/opt/DigitalHumanDemo`。使用其他路径时同步修改服务单元中的工作目录、Python 路径。

```bash
git clone https://github.com/yangyi626/DigitalHumanDemo.git /opt/DigitalHumanDemo
cd /opt/DigitalHumanDemo
cp .env.example .env
# 编辑 .env，填写 BOSON_API_KEY 与 DEEPSEEK_API_KEY。
python3.11 run.py --check
```

默认读取 `web/` 和 `avatar/` 原始资源，不需要 Node.js 或网页发布快照。

以管理员身份创建运行用户并安装服务：

```bash
id dhlive-expert >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin dhlive-expert
chown dhlive-expert:dhlive-expert .env
chmod 600 .env
install -m 644 deploy/dhlive-expert.service /etc/systemd/system/dhlive-expert.service
systemctl daemon-reload
systemctl enable --now dhlive-expert.service
curl -fsS http://127.0.0.1:8789/api/status
```

运行用户需要能够遍历项目目录、读取源码和人物资源，并执行 `.venv/bin/python`。`missing=[]` 表示 Key 已配置，真实可用性需通过问答验证。

## HTTPS 与 Nginx

为自己的域名配置可信 HTTPS 证书。在 Nginx 的 `http` 配置作用域中定义 WebSocket 升级变量：

```nginx
map $http_upgrade $dhlive_connection_upgrade {
    default upgrade;
    '' close;
}
```

将 `deploy/nginx-limit.conf` 的内容放入 `http` 作用域，例如 `/etc/nginx/conf.d/dhlive-expert.conf`；把 `deploy/nginx-route.conf` 的内容放到自己的 HTTPS `server` 块内。入口为 `https://你的域名/dhlive/`。该路径保留结尾斜杠，确保页面的相对资源和 WebSocket 地址正确。

```bash
nginx -t
systemctl reload nginx
```

用手机或另一台设备访问时必须使用可信 HTTPS，浏览器才允许麦克风。代理的 `Host` 必须保留客户端主机名和端口，以通过同源校验。不要将 `.env` 放到静态资源目录。

## 可选的网页版本发布

生产环境可使用 Node.js 做脚本语法校验，发布不可变网页快照：

```bash
.venv/bin/python deploy/publish_web.py
# 用上一步输出的版本号激活。
.venv/bin/python deploy/publish_web.py --activate VERSION
```

首次切换为快照发布时重启服务，让 `/releases/` 资源路由生效。后续纯网页版本切换无需重启。`releases/` 和 `active-release` 都是部署产物，无需提交 Git。

后端或依赖更新后需要重启服务，会中断当前通话。更新时保留自己的 `.env`；依赖可通过 `python3.11 run.py --check` 重新安装。

```bash
systemctl restart dhlive-expert.service
journalctl -u dhlive-expert.service -n 80 --no-pager
```
