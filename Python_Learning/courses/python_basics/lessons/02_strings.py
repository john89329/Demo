"""Lesson 02: Strings — text manipulation."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="02_strings",
        title="第2课：字符串操作 — 玩转文本",
        duration="30分钟",
        content="""
## 字符串 (str)

字符串就是**一串字符**，用单引号 `'...'` 或双引号 `"..."` 包裹。

```python
s1 = 'hello'
s2 = "世界"
s3 = "I'm a student"     # 内含单引号时用双引号包裹
```

---

### 字符串拼接与重复

```python
"你好" + "世界"       # "你好世界"  — 用 + 拼接
"哈" * 3              # "哈哈哈"    — 用 * 重复
```

---

### f-string（最推荐的格式化方式）

```python
name = "小明"
score = 95
print(f"{name}的成绩是{score}分")
# 输出: 小明的成绩是95分

# 还可以做运算
print(f"明年{2025 + 1}年")
```

---

### 常用字符串方法

```python
text = "  Python 编程  "

text.strip()         # "Python 编程"    去除两端空格
text.upper()         # "  PYTHON 编程  "  全大写
text.lower()         # "  python 编程  "  全小写
text.replace("Python", "Java")   # 替换
text.startswith("  P")           # True
text.endswith("  ")              # True
text.find("Python")              # 2 (找到的位置)
```

---

### 字符串索引与切片

```python
s = "Hello"

s[0]     # 'H'   第一个字符（索引从0开始）
s[-1]    # 'o'   倒数第一个
s[1:4]   # 'ell' 索引1到3（不包含4）
s[:3]    # 'Hel' 开头到索引2
s[2:]    # 'llo' 索引2到末尾
s[::-1]  # 'olleH' 反转！
```

---

### 获取字符串长度

```python
len("Python")     # 6
len("你好")        # 2
```
""",
        exercises=[
            Exercise(
                instruction="用 f-string 打印：'我叫XXX，最喜欢的语言是Python，已经学了X天了'。名字自取，天数自取。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint='name = "小明"\ndays = 1\nprint(f"我叫{name}...")',
                expected_output="",
                reference_answer='name = "小明"\ndays = 1\nprint(f"我叫{name}，最喜欢的语言是Python，已经学了{days}天了")',
            ),
            Exercise(
                instruction="给定字符串 s = 'Hello, Python!'，请打印：它的长度、它的前5个字符、它的反转",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="len(s), s[:5], s[::-1]",
                expected_output="",
                reference_answer='s = "Hello, Python!"\nprint(len(s))\nprint(s[:5])\nprint(s[::-1])',
            ),
            Exercise(
                instruction="选择题：'Hello'[1:4] 的结果是什么？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 'Hell'",
                    "B. 'ell'",
                    "C. 'Hel'",
                    "D. 'ello'",
                ],
                correct_answer="B",
            ),
        ],
    )
