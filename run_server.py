import os
import sys
import argparse

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保在project目录
os.chdir(os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from backend.config import DEBUG, HOST, PORT


def main():
    parser = argparse.ArgumentParser(description="启动 FastAPI 后端服务")
    parser.add_argument("--host", default=HOST, help=f"监听地址 (默认 {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"端口号 (默认 {PORT})")
    parser.add_argument("--debug", action="store_true", default=DEBUG, help="开启 FastAPI 自动重载")
    parser.add_argument("--no-debug", action="store_false", dest="debug", help="关闭 FastAPI 自动重载")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "backend.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.debug,
        workers=1,
    )


if __name__ == "__main__":
    main()
