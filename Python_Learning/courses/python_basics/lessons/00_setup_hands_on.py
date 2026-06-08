"""Lesson 00: 环境准备实操 — 确认 Python 能用，真正动手跑起来。"""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="00_setup",
        title="第0课：环境准备 — 让 Python 跑起来",
        duration="20分钟",
        content="""
## 欢迎来到 Python 的世界！

在写代码之前，我们首先要确保 Python 在你的电脑上能正常工作。

这就像学开车之前，先确认车能发动一样——这是最关键的第一步。

---

### 环节一：打开终端（命令行）

终端是你和电脑"对话"的窗口，Python 要通过它来运行。

**Windows 用户：**
- 按 `Win + R`，输入 `cmd` 回车，就打开了命令提示符
- 或者搜索框搜 "PowerShell" 打开

**Mac 用户：**
- 打开 "终端" (Terminal) 应用

打开后你会看到一个黑色的窗口，上面有一个闪烁的光标——这就是命令行。

---

### 环节二：确认 Python 已安装

在终端中输入以下命令并回车：

```
python --version
```

**注意：** Mac 用户可能需要输入 `python3 --version`

如果显示了版本号（比如 `Python 3.x.x`），说明 Python 已经装好了！

如果提示 "命令不存在"，你需要先安装 Python：去 https://python.org 下载安装。

---

### 环节三：Python 交互模式（REPL）

REPL = Read（读取）→ Evaluate（执行）→ Print（打印）→ Loop（循环）

在终端输入 `python`（或 `python3`）并回车，你会看到：

```
Python 3.x.x ...
Type "help", "copyright", "credits" or "license" for more information.
>>>
```

`>>>` 就是 Python 的提示符，表示它在等待你的指令。

试试输入以下内容（每行输完按回车）：

```
>>> print("Hello, Python!")
>>> 1 + 2
>>> "你好" + "世界"
>>> exit()
```

`exit()` 会退出交互模式。

---

### 环节四：运行第一个 .py 脚本

交互模式适合临时测试，但真正的程序要写在 `.py` 文件里。

1. 创建一个文本文件，命名为 `hello.py`
2. 在里面写一行代码：`print("Hello, Python!")`
3. 在终端中，进入到文件所在目录，运行：

```
python hello.py
```

你就能看到输出结果了！

---

### 小总结

| 模式 | 怎么进入 | 适合做什么 |
|------|----------|-----------|
| 交互模式 (REPL) | 终端输入 `python` | 临时测试、快速计算 |
| 脚本模式 | 写 `.py` 文件，`python 文件名.py` | 编写完整程序 |
""",
        exercises=[
            Exercise(
                instruction="你的电脑上安装的是 Python 哪个版本？在本工具中直接运行代码，看看输出什么。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="使用 sys.version 或者直接打印一条消息",
                setup_code="import sys",
                expected_output="",
                reference_answer="print(sys.version)",
            ),
            Exercise(
                instruction="选择题：在终端输入 `python` 后看到的 `>>>` 是什么？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 错误提示",
                    "B. Python 的提示符，表示等待输入命令",
                    "C. 装饰符号",
                    "D. 文件路径",
                ],
                correct_answer="B",
            ),
            Exercise(
                instruction="自由练习：写一个简单的脚本，打印一句你想对 Python 说的话！",
                exercise_type=ExerciseType.FREE_PRACTICE,
                hint="用 print() 函数",
                reference_answer='print("Python, 我来啦！")',
            ),
        ],
    )
