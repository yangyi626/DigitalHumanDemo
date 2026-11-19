"""Create the local environment, check configuration, and start the demo."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.venv'
PYTHON = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def main():
    parser = argparse.ArgumentParser(description='安装依赖并启动数字人 Demo（Python 3.11 / 3.12）')
    parser.add_argument('--check', action='store_true', help='安装依赖并检查配置，不启动服务')
    parser.add_argument('--host', help='覆盖 .env 中的监听地址')
    parser.add_argument('--port', type=int, help='覆盖 .env 中的监听端口')
    options = parser.parse_args()
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        parser.exit(1, '请使用 Python 3.11 或 3.12 运行此脚本，例如 python3.11 run.py。\n')

    if Path(sys.prefix).resolve() != ENV.resolve():
        if not PYTHON.exists():
            print('正在创建 .venv …', flush=True)
            venv.EnvBuilder(with_pip=True).create(ENV)
        lock = ROOT / 'requirements-lock.txt'
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        marker = ENV / '.requirements-sha256'
        if not marker.exists() or marker.read_text().strip() != digest:
            print('正在安装运行依赖 …', flush=True)
            subprocess.run([str(PYTHON), '-m', 'pip', 'install', '--disable-pip-version-check',
                            '-r', str(lock)], check=True)
            marker.write_text(digest + '\n')
        os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

    from dotenv import load_dotenv

    config = ROOT / '.env'
    if not config.exists():
        # Exclusive creation preserves an existing configuration even if another
        # startup creates it at the same time. Never copy a server's real keys.
        try:
            with config.open('x', encoding='utf-8') as stream:
                stream.write((ROOT / '.env.example').read_text(encoding='utf-8'))
            config.chmod(0o600)
            print('已创建 .env，请填写 BOSON_API_KEY 和 DEEPSEEK_API_KEY。', flush=True)
        except FileExistsError:
            pass
    load_dotenv(config)
    missing = [key for key in ('BOSON_API_KEY', 'DEEPSEEK_API_KEY') if not os.getenv(key, '').strip()]
    if missing:
        parser.exit(1, '请在 .env 中填写：' + '、'.join(missing) + '，然后再次运行。\n')
    if options.host:
        os.environ['HOST'] = options.host
    if options.port is not None:
        os.environ['PORT'] = str(options.port)
    host = os.getenv('HOST', '127.0.0.1')
    try:
        port = int(os.getenv('PORT', '8789'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.exit(1, 'PORT 必须是 1 至 65535 之间的整数。\n')
    from server import make_app
    app = make_app()
    if options.check:
        print('依赖、配置及网页目录检查通过。Key 是否有效需通过实际问答验证。')
        return

    import logging
    from aiohttp import web
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    address = 'localhost' if host in ('127.0.0.1', '0.0.0.0', '::1', '::') else host
    print(f'请在浏览器打开 http://{address}:{port}/，按 Ctrl+C 停止。', flush=True)
    web.run_app(app, host=host, port=port, access_log=None)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
