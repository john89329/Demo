"""Lesson 11: File I/O — reading and writing files."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="11_file_io",
        title="第11课：文件操作 — 读写文件",
        duration="25分钟",
        content="""
## 为什么要读写文件？

程序的变量在运行结束后就消失了。要**持久化数据**，就需要存到文件中。

---

### 打开文件的模式

| 模式 | 含义 | 文件不存在时 |
|------|------|-------------|
| `'r'` | 只读 | 报错 |
| `'w'` | 只写（覆盖） | 自动创建 |
| `'a'` | 追加（不覆盖） | 自动创建 |
| `'r+'` | 读写 | 报错 |

默认是文本模式。加 `b` 表示二进制模式（如图片）：`'rb'`, `'wb'`

---

### 读文件

```python
# 方法1：读取全部内容
with open("test.txt", "r", encoding="utf-8") as f:
    content = f.read()
    print(content)

# 方法2：逐行读取
with open("test.txt", "r", encoding="utf-8") as f:
    for line in f:
        print(line.strip())    # strip() 去掉换行符

# 方法3：读取所有行到列表
with open("test.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()     # 每行是一个列表元素
```

---

### 写文件

```python
# 覆盖写入
with open("output.txt", "w", encoding="utf-8") as f:
    f.write("第一行\n")
    f.write("第二行\n")

# 追加写入
with open("output.txt", "a", encoding="utf-8") as f:
    f.write("追加的内容\n")
```

---

### with 语句的好处

```python
# with 会自动关闭文件，即使出错也会关
# 不推荐的写法:
f = open("test.txt", "r")
content = f.read()
f.close()            # 如果中间出错，close() 不会执行

# 推荐的写法:
with open("test.txt", "r") as f:
    content = f.read()
# 出了 with 块，文件自动关闭
```

---

### 注意事项

- 读写中文文件一定要指定 `encoding="utf-8"`
- 文件路径可以用相对路径 `"./data/file.txt"` 或绝对路径
- Windows 路径中的 `\` 要写为 `\\` 或用 `/` 代替
""",
        exercises=[
            Exercise(
                instruction="写代码：创建一个文件 'my_note.txt'，写入 'Python 真好玩！' 和 '今天学习文件操作。' 两行，然后读取并打印文件内容。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="先用 'w' 模式写，再用 'r' 模式读",
                expected_output="",
                reference_answer="with open('my_note.txt', 'w', encoding='utf-8') as f:\n    f.write('Python 真好玩！\\n')\n    f.write('今天学习文件操作。\\n')\n\nwith open('my_note.txt', 'r', encoding='utf-8') as f:\n    print(f.read())",
            ),
            Exercise(
                instruction="选择题：用 'a' 模式打开一个已存在的文件进行写操作，会发生什么？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 覆盖原有内容",
                    "B. 追加到文件末尾",
                    "C. 报错",
                    "D. 清空文件",
                ],
                correct_answer="B",
            ),
            Exercise(
                instruction="自由练习：写一个简单的日记程序——每次运行时，让用户输入一句话，追加保存到 diary.txt 中。",
                exercise_type=ExerciseType.FREE_PRACTICE,
                hint="用 'a' 模式打开文件",
                reference_answer="line = input('今天想记录什么？')\nwith open('diary.txt', 'a', encoding='utf-8') as f:\n    f.write(line + '\\n')",
            ),
        ],
    )
