"""
CLI 入口：加载菜单状态并运行批量图片处理。
"""
from __future__ import annotations

import atexit
import logging
import sys

from core.constants import AppState, DEBUG
from cli.setup import SEPARATE_LINE, config, root_menu
from services.processing import list_input_images, process_images
from utils_pkg import stop_exiftool

# 程序退出时关闭 ExifTool 进程
atexit.register(stop_exiftool)


def processing() -> AppState:
    """处理图片并返回下一个状态。"""
    file_list = list_input_images(config.get_input_dir())
    print('当前共有 {} 张图片待处理'.format(len(file_list)))
    try:
        process_images(file_list, config.get_output_dir(), config)
    except Exception as e:
        logging.exception(f'Error: {str(e)}')
        if DEBUG:
            raise e
        print('\nError: 图片处理失败，请检查日志')

    option = input('处理完成，文件已输出至 output 文件夹中，输入【r】返回主菜单，输入【x】退出程序\n')
    if DEBUG:
        return AppState.EXIT
    if option.lower() == 'x':
        return AppState.EXIT
    return AppState.MAIN_MENU


def main() -> None:
    """主循环。"""
    state = AppState.MAIN_MENU
    current_menu = root_menu

    print(SEPARATE_LINE)
    print('''
本工具为开源工具，遵循 Apache 2.0 License 发布。如果您在使用过程中遇到问题，请联系作者：
GitHub: @leslievan
Bilibili: @吨吨吨的半夏
项目介绍：https://www.bilibili.com/video/BV11A411U7Kn
项目地址：https://github.com/leslievan/semi-utils
项目介绍（博客）：https://lsvm.xyz/2023/02/semi-utils-intro/
项目发布页：https://docs.qq.com/sheet/DTXF5c2lHeUZYREtw
''')

    while True:
        try:
            if state == AppState.MAIN_MENU:
                print(SEPARATE_LINE)
                current_menu.display()
                print(SEPARATE_LINE)

                user_input = input(
                    '输入【y 或回车】按照当前设置开始处理图片，输入【数字】修改设置，'
                    '输入【r】返回上一层菜单，输入【x】退出程序\n'
                )

                if user_input in ('y', ''):
                    state = AppState.PROCESSING
                elif user_input.lower() == 'x':
                    sys.exit(0)
                elif user_input.lower() == 'r':
                    current_menu = current_menu.get_parent()
                elif user_input.isdigit() and 1 <= int(user_input) <= len(current_menu.components):
                    current_menu = current_menu.components[int(user_input) - 1]
                    if current_menu.is_leaf():
                        current_menu.run()
                        current_menu = root_menu
                else:
                    print('输入错误，请重新输入')

            elif state == AppState.PROCESSING:
                print(SEPARATE_LINE)
                state = processing()

            elif state == AppState.EXIT:
                sys.exit(0)

            elif state == AppState.ERROR_EXIT:
                sys.exit(1)

            # 保存配置
            config.save()

        except Exception as e:
            logging.exception(f'Error: {str(e)}')
            if DEBUG:
                raise e


if __name__ == '__main__':
    main()
