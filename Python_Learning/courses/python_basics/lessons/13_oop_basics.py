"""Lesson 13: OOP Basics — classes and objects."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="13_oop",
        title="第13课：类与对象 — 面向对象入门",
        duration="35分钟",
        content="""
## 什么是面向对象编程（OOP）？

OOP 的核心思想：把**数据**和**操作数据的方法**打包在一起，形成"对象"。

类比：蓝图 (class) 和 汽车 (object)
- **类 (class)** = 设计蓝图
- **对象 (object)** = 按蓝图制造出来的具体实例
- **属性 (attribute)** = 对象的数据（颜色、速度等）
- **方法 (method)** = 对象能做的事（加速、刹车等）

---

### 定义一个类

```python
class Dog:
    '''一只狗'''

    def __init__(self, name, age):
        '''初始化方法 -- 创建对象时自动调用'''
        self.name = name      # 实例属性
        self.age = age

    def bark(self):
        '''狗叫'''
        return f"{self.name} 说: 汪汪!"

    def birthday(self):
        '''过生日，年龄+1'''
        self.age += 1
        return f"{self.name} 现在 {self.age} 岁了"

# 创建实例
my_dog = Dog("旺财", 3)
print(my_dog.bark())         # 旺财 说: 汪汪!
print(my_dog.birthday())     # 旺财 现在 4 岁了
print(my_dog.age)            # 4
```

---

### 关键概念

#### `self` — 指代"自己"
每个方法第一个参数总是 `self`，代表调用这个方法的实例本身。

```python
my_dog.bark()      # 等价于 Dog.bark(my_dog)
```

#### `__init__` — 构造方法
创建对象时自动调用，用于初始化属性。

#### 实例属性 vs 类属性

```python
class Student:
    school = "第一中学"    # 类属性 — 所有实例共享

    def __init__(self, name):
        self.name = name   # 实例属性 — 每个实例独有

s1 = Student("小明")
s2 = Student("小红")
print(s1.school)     # "第一中学"
print(s2.school)     # "第一中学"
```

---

### 继承 — 复用代码

```python
class Animal:
    def __init__(self, name):
        self.name = name

    def speak(self):
        return f"{self.name} 发出了声音"

class Cat(Animal):          # Cat 继承自 Animal
    def speak(self):
        return f"{self.name} 说: 喵喵!"

class Dog(Animal):
    def speak(self):
        return f"{self.name} 说: 汪汪!"

cat = Cat("小花")
dog = Dog("旺财")
print(cat.speak())    # 小花 说: 喵喵!
print(dog.speak())    # 旺财 说: 汪汪!
```

- **父类** (基类) = 被继承的类
- **子类** (派生类) = 继承父类的类
- **方法重写** (override) = 子类重新定义父类的同名方法

---

### 为什么用 OOP？

1. **组织复杂代码** — 相关数据和函数放在一起
2. **复用代码** — 通过继承避免重复
3. **更贴近现实思维** — 万物皆对象
""",
        exercises=[
            Exercise(
                instruction="定义一个类 Book，有属性 title（书名）和 author（作者），以及一个方法 info() 返回格式化的描述（如'《三体》作者：刘慈欣'）。创建一本你喜欢的书并调用 info()。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="def __init__(self, title, author): ...; def info(self): return f'《{self.title}》作者：{self.author}'",
                expected_output="",
                reference_answer='class Book:\n    def __init__(self, title, author):\n        self.title = title\n        self.author = author\n    def info(self):\n        return f"《{self.title}》作者：{self.author}"\n\nbook = Book("三体", "刘慈欣")\nprint(book.info())',
            ),
            Exercise(
                instruction="选择题：以下关于 `self` 的描述哪个是正确的？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. self 是一个 Python 关键字",
                    "B. self 指向类本身",
                    "C. self 指向调用该方法的实例对象",
                    "D. self 可以省略不写",
                ],
                correct_answer="C",
            ),
            Exercise(
                instruction="自由练习：定义一个类 Counter（计数器），有属性 value（初始为 0），方法 increment()（+1）和 decrement()（-1），方法 reset()（归零）。创建实例并测试。",
                exercise_type=ExerciseType.FREE_PRACTICE,
                hint="__init__(self): self.value = 0",
                reference_answer='class Counter:\n    def __init__(self):\n        self.value = 0\n    def increment(self):\n        self.value += 1\n    def decrement(self):\n        self.value -= 1\n    def reset(self):\n        self.value = 0\n\nc = Counter()\nc.increment()\nc.increment()\nprint(c.value)  # 2',
            ),
        ],
    )
