"""Lesson 06: Lists — ordered collections."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="06_lists",
        title="第6课：列表 — 数据的收纳盒",
        duration="30分钟",
        content="""
## 什么是列表 (list)？

列表是有序的、可变的容器，用 `[]` 表示。

```python
fruits = ["苹果", "香蕉", "橘子"]
numbers = [1, 2, 3, 4, 5]
mixed = [1, "hello", True, 3.14]    # 可以混合不同类型
empty = []                            # 空列表
```

---

### 访问元素（索引）

```python
fruits = ["苹果", "香蕉", "橘子"]

fruits[0]     # "苹果"  索引从 0 开始！
fruits[1]     # "香蕉"
fruits[-1]    # "橘子"  负数索引从末尾倒数
fruits[-2]    # "香蕉"
```

---

### 修改、添加、删除

```python
lst = ["a", "b", "c"]

# 修改
lst[0] = "A"             # ["A", "b", "c"]

# 添加
lst.append("d")          # 末尾添加 → ["A", "b", "c", "d"]
lst.insert(1, "X")       # 在索引1插入 → ["A", "X", "b", "c", "d"]

# 删除
lst.remove("X")          # 按值删除第一个匹配
popped = lst.pop()       # 删除并返回最后一个 → "d"
popped = lst.pop(0)      # 删除并返回索引0的元素
del lst[1]               # 直接删除索引1的元素
```

---

### 列表切片

```python
nums = [0, 1, 2, 3, 4, 5]

nums[1:4]    # [1, 2, 3]  索引1-3（不含4）
nums[:3]     # [0, 1, 2]  开头到索引2
nums[3:]     # [3, 4, 5]  索引3到末尾
nums[::2]    # [0, 2, 4]  每隔一个
nums[::-1]   # [5, 4, 3, 2, 1, 0]  反转！
```

---

### 常用操作

```python
nums = [3, 1, 4, 1, 5]

len(nums)         # 5       长度
sum(nums)         # 14      求和
max(nums)         # 5       最大值
min(nums)         # 1       最小值
nums.sort()       # 原地排序 → [1, 1, 3, 4, 5]
sorted(nums)      # 返回新排序列表，原列表不变
3 in nums         # True    检查是否包含
nums.count(1)     # 2       统计出现次数
```

---

### 列表的遍历

```python
for fruit in ["苹果", "香蕉", "橘子"]:
    print(f"我喜欢吃{fruit}")
```
""",
        exercises=[
            Exercise(
                instruction="创建一个列表 [10, 20, 30, 40, 50]，然后：在末尾添加 60，在开头插入 0，删除 30，反转列表，打印最终结果。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="append, insert, remove, reverse",
                expected_output="",
                reference_answer='lst = [10, 20, 30, 40, 50]\nlst.append(60)\nlst.insert(0, 0)\nlst.remove(30)\nlst.reverse()\nprint(lst)',
            ),
            Exercise(
                instruction="打印列表 [1, 2, 3, 4, 5] 的：长度、最大值、和、以及索引2到4的切片",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="len, max, sum, 切片 [2:5]",
                expected_output="",
                reference_answer='lst = [1, 2, 3, 4, 5]\nprint(len(lst))\nprint(max(lst))\nprint(sum(lst))\nprint(lst[2:5])',
            ),
            Exercise(
                instruction="选择题：list.pop() 会做什么？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 删除第一个元素",
                    "B. 删除最后一个元素并返回它",
                    "C. 删除所有元素",
                    "D. 返回列表长度",
                ],
                correct_answer="B",
            ),
        ],
    )
