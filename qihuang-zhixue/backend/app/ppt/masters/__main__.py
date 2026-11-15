"""``python -m app.ppt.masters``：把母版目录写成前端 pptMasters.ts。"""

from . import write_typescript

if __name__ == "__main__":
    print(write_typescript())
