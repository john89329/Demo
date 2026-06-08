"""Lesson 07: Loops — repeating code efficiently."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="07_loops",
        title="第7课：循环 — 让代码自动重复",
        duration="30分钟",
        content="""
## for 循环 — 遍历可迭代对象

```python
# 遍历列表
fruits = ["苹果", "香蕉", "橘子"]
for fruit in fruits:
    print(f"我喜欢吃{fruit}")

# 遍历字符串
for char in "Python":
    print(char)          # 逐个字符打印

# 遍历 range()
for i in range(5):       # 0, 1, 2, 3, 4
    print(i)

for i in range(2, 6):    # 2, 3, 4, 5  (从2开始，到6之前)
    print(i)

for i in range(0, 10, 2):  # 0, 2, 4, 6, 8  (步长为2)
    print(i)
```

---

### range() 的三个参数

```python
range(start, stop, step)
# start: 起始值（默认0）
# stop:  结束值（不包含）
# step:  步长（默认1）
```

---

### while 循环 — 满足条件就一直跑

```python
count = 0
while count < 3:
    print(f"第{count + 1}次循环")
    count += 1

# 输出:
# 第1次循环
# 第2次循环
# 第3次循环
```

**小心死循环！** 一定要确保循环条件最终会变成 False。

---

### break 和 continue

```python
# break: 直接跳出循环
for i in range(10):
    if i == 5:
        break       # 到5就停了
    print(i)         # 只打印 0,1,2,3,4

# continue: 跳过本次，继续下一次
for i in range(5):
    if i == 2:
        continue     # 跳过2
    print(i)         # 打印 0,1,3,4
```

---

### for-else 和 while-else

```python
for i in range(3):
    print(i)
else:
    print("循环正常结束")   # 没有被 break 打断时执行
```
""",
        exercises=[
            Exercise(
                instruction="用 for 循环打印 1 到 10 的每个数字和它的平方（数字: 平方）。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="for i in range(1, 11): print(f'{i}: {i**2}')",
                expected_output="",
                reference_answer='for i in range(1, 11):\n    print(f"{i}: {i**2}")',
            ),
            Exercise(
                instruction="用 while 循环计算 1+2+3+...+100 的和并打印。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="total = 0; i = 1; while i <= 100: total += i; i += 1",
                expected_output="5050",
                reference_answer='total = 0\ni = 1\nwhile i <= 100:\n    total += i\n    i += 1\nprint(total)',
            ),
            Exercise(
                instruction="选择题：range(3, 8) 会生成哪些数字？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 3, 4, 5, 6, 7, 8",
                    "B. 3, 4, 5, 6, 7",
                    "C. 4, 5, 6, 7, 8",
                    "D. 0, 1, 2",
                ],
                correct_answer="B",
            ),
        ],
    )
