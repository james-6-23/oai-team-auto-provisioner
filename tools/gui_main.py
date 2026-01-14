"""GUI 打包入口（用于 PyInstaller onefile）。

说明：
- 不直接用包内模块作为 PyInstaller 入口，避免相对导入报错。
- 当前入口指向 pywebview 版本 GUI；源码运行仍推荐：`python -m webview_gui`
"""

from webview_gui.main import main


if __name__ == "__main__":
    main()
