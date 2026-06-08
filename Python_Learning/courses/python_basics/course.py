"""Course registration: Python Basics."""

import os
import importlib

from courses.base import Course, Difficulty


def register_course() -> Course:
    """Called by the engine to discover this course.

    Auto-discovers all lessons in the lessons/ directory by scanning for
    files matching the pattern NN_name.py and calling their create_lesson().
    """

    lessons_dir = os.path.dirname(os.path.abspath(__file__))
    lessons_path = os.path.join(lessons_dir, "lessons")

    lessons = []
    # Scan for lesson files
    for filename in sorted(os.listdir(lessons_path)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        # Convert filename to module path
        # e.g. "00_setup_hands_on.py" -> "courses.python_basics.lessons.00_setup_hands_on"
        module_name = f"courses.python_basics.lessons.{filename[:-3]}"
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, "create_lesson"):
                lesson = mod.create_lesson()
                lessons.append(lesson)
        except Exception as e:
            import traceback
            print(f"  [WARN] Failed to load lesson '{filename}': {e}")
            traceback.print_exc()

    return Course(
        course_id="python_basics",
        name="Python 基础入门",
        description="""
从零开始学习 Python 编程！本课程涵盖 Python 的核心语法和编程思想。

14 节课，从环境搭建到面向对象编程，每节课都有互动练习，让你边学边练。

完成本课程后，你将能够：
  • 理解变量、数据类型、运算符
  • 使用条件判断和循环控制程序流程
  • 操作列表、字典、元组和集合
  • 编写和使用函数
  • 读写文件
  • 处理程序异常
  • 理解并编写简单的面向对象代码
""".strip(),
        difficulty=Difficulty.BEGINNER,
        lessons=lessons,
    )
