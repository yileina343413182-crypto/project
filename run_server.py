import os
import sys
import argparse

# 确保在project目录
os.chdir(os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from backend.config import DEBUG, HOST, PORT
from backend.app import create_app


def main():
    parser = argparse.ArgumentParser(description="启动 Flask 后端服务")
    parser.add_argument("--host", default=HOST, help=f"监听地址 (默认 {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"端口号 (默认 {PORT})")
    parser.add_argument("--debug", action="store_true", default=DEBUG, help="开启 Flask 调试模式")
    parser.add_argument("--no-debug", action="store_false", dest="debug", help="关闭 Flask 调试模式")
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
