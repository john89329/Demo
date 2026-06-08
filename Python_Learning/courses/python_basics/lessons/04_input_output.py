"""Lesson 04: Input and Output — talking to the user."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="04_io",
        title="第4课：输入与输出 — 和程序对话",
        duration="20分钟",
        content="""
## 输出：print() 函数

```python
print("Hello")                        # 打印字符串
print("你好", "世界")                  # 多个参数，空格分隔
print("第一行", end="")               # 默认 end="\n"，改掉则不换行
print("继续")
# 输出: 你好 世界
#       第一行继续
```

---

## 输入：input() 函数

```python
name = input("你叫什么名字？")    # 显示提示，等待用户输入
print(f"你好，{name}！")
```

**重要：input() 返回的永远是字符串！**

```python
age = input("你几岁？")    # 用户输入 "25"
age = int(age)             # 转为整数才能做数学运算
print(f"明年你 {age + 1} 岁")
```

或者一行搞定：

```python
age = int(input("你几岁？"))
```

---

### 常见输入转换

```python
int(input(...))      # 转为整数
float(input(...))    # 转为浮点数
input(...).strip()   # 去除首尾空格
```

---

### 实用技巧：打印分隔线

```python
print("-" * 30)       # 打印30个横线
print("=" * 50)       # 打印50个等号
```

---

### 注意

如果你需要程序暂停等用户按回车，可以这样：

```python
input("按回车继续...")
```

这在命令行交互中很常用！
""",
        exercises=[
            Exercise(
                instruction="写一个程序：询问用户的名字和年龄，然后打印'XXX你好，你明年就X+1岁了！'",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="用 input() 获取输入，用 int() 转换年龄，用 f-string 输出",
                expected_output="",
                reference_answer='name = input("你的名字：") \nage = int(input("你的年龄：")) \nprint(f"{name}你好，你明年就{age + 1}岁了！")',
            ),
            Exercise(
                instruction="选择题：input() 返回的数据类型是什么？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. int",
                    "B. float",
                    "C. str",
                    "D. 看用户输入什么决定",
                ],
                correct_answer="C",
            ),
            Exercise(
                instruction="自由练习：写一个简单的加法计算器——让用户输入两个数字，打印它们的和。",
                exercise_type=ExerciseType.FREE_PRACTICE,
                hint="两数都需要 int() 或 float() 转换",
                reference_answer='a = float(input("第一个数："))\nb = float(input("第二个数："))\nprint(f"{a} + {b} = {a + b}")',
            ),
        ],
    )
