"""Course engine — discovers, loads, and runs courses."""

import importlib
import os
import sys
import traceback
from typing import Optional

# Ensure project root in path for imports (must be before other local imports)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.display import (
    header, success, error, info, warning, highlight,
    prompt, wait_enter, section, code_block,
    render_content, menu, divider, Color,
)
from core.progress import Progress
from courses.base import Course, Lesson, Exercise, ExerciseType


class Engine:
    """Main engine that loads and runs courses."""

    def __init__(self):
        self.progress = Progress(data_dir=os.path.join(PROJECT_ROOT, "data"))
        self.courses: dict[str, Course] = {}

    def discover_courses(self) -> dict[str, Course]:
        """Auto-discover all courses under courses/ directory."""
        courses_dir = os.path.join(PROJECT_ROOT, "courses")
        discovered = {}

        for item in os.listdir(courses_dir):
            item_path = os.path.join(courses_dir, item)
            if not os.path.isdir(item_path):
                continue
            # Skip the base module, __pycache__, etc.
            if item.startswith("_") or item.startswith("."):
                continue
            # Check for course.py registration
            course_file = os.path.join(item_path, "course.py")
            if not os.path.exists(course_file):
                continue

            try:
                module_name = f"courses.{item}.course"
                mod = importlib.import_module(module_name)
                if hasattr(mod, "register_course"):
                    course = mod.register_course()
                    discovered[course.course_id] = course
                    info(f"已加载课程: {course.name}")
            except Exception as e:
                warning(f"加载课程失败 '{item}': {e}")
                traceback.print_exc()

        self.courses = discovered
        return discovered

    def run(self):
        """Main entry point — show course selection menu."""
        self.discover_courses()

        if not self.courses:
            error("没有找到任何课程！")
            wait_enter()
            return

        while True:
            print(Color.RESET)
            header("🐍 Python 自学互动工具")
            print(f"\n{Color.BOLD}欢迎！选择一个课程开始学习：{Color.RESET}\n")

            course_list = list(self.courses.values())
            names = []
            for c in course_list:
                prog = self.progress.get_course_progress(c.course_id)
                done = len(prog.get("completed_lessons", []))
                total = c.get_total_lessons()
                status = f"[{done}/{total}]"
                difficulty = c.difficulty
                names.append(f"{c.name}  {Color.DIM}{difficulty}  {status}{Color.RESET}")

            choice = menu(names, "可用课程")
            if choice == 0:
                print(f"\n{Color.GREEN}再见！继续加油学习！{Color.RESET}\n")
                break

            selected = course_list[choice - 1]
            self.run_course(selected)

    def run_course(self, course: Course):
        """Run a complete course session."""
        from .display import clear_screen

        clear_screen()
        header(f"📚 {course.name}")
        print(f"\n{course.description}")
        print(f"难度: {highlight(course.difficulty)}")
        print(f"课节数: {len(course.lessons)}")

        prog_data = self.progress.get_course_progress(course.course_id)
        completed = prog_data.get("completed_lessons", [])

        if prog_data.get("started_at") and not prog_data.get("completed"):
            info(f"你之前已完成了 {len(completed)} 节课，可以继续学习。")

        # Lesson selection menu
        while True:
            print(f"\n{divider()}")
            lesson_names = []
            for i, lesson in enumerate(course.lessons):
                lid = lesson.lesson_id
                marker = " ✓" if lid in completed else ""
                current = " ▶" if lid == prog_data.get("current_lesson") and lid not in completed else ""
                lesson_names.append(f"{lesson.title} ({lesson.duration}){marker}{current}")

            choice = menu(lesson_names, "课节目录")
            if choice == 0:
                # Check if all lessons done
                if len(completed) == len(course.lessons):
                    self.progress.complete_course(course.course_id)
                    success("🎉 恭喜你完成了全部课程！")
                break

            lesson = course.lessons[choice - 1]
            self.run_lesson(course, lesson)

            # Check course completion
            prog_data = self.progress.get_course_progress(course.course_id)
            completed = prog_data.get("completed_lessons", [])
            if len(completed) == len(course.lessons):
                self.progress.complete_course(course.course_id)
                success("🎉 恭喜你完成了全部课程！")
                wait_enter()
                break

    def run_lesson(self, course: Course, lesson: Lesson):
        """Run a single lesson — show content, run exercises."""
        from .display import clear_screen

        self.progress.start_course(course.course_id)
        self.progress.set_current_lesson(course.course_id, lesson.lesson_id)

        clear_screen()
        header(f"📖 {lesson.title}")
        print(f"{Color.DIM}预计时长: {lesson.duration}{Color.RESET}")
        print(divider())

        # Render content
        render_content(lesson.content)

        # Run exercises
        if lesson.exercises:
            print(f"\n{divider()}")
            header("✏️ 练习题")

        correct_count = 0
        total_count = 0

        for i, exercise in enumerate(lesson.exercises, 1):
            print(f"\n{Color.BOLD}练习 {i}/{len(lesson.exercises)}{Color.RESET}")
            result = self._run_exercise(exercise)
            if result:
                correct_count += 1
            total_count += 1

        # Save progress
        score = {
            "correct": correct_count,
            "total": total_count,
            "percentage": round(correct_count / max(total_count, 1) * 100),
        }
        self.progress.complete_lesson(course.course_id, lesson.lesson_id, score)

        if total_count > 0:
            pct = score["percentage"]
            if pct >= 80:
                success(f"练习得分: {correct_count}/{total_count} ({pct}%) — 太棒了！")
            elif pct >= 50:
                info(f"练习得分: {correct_count}/{total_count} ({pct}%) — 继续加油！")
            else:
                warning(f"练习得分: {correct_count}/{total_count} ({pct}%) — 建议复习后再来。")

        print(f"\n{Color.GREEN}{Color.BOLD}✅ 本课完成！{Color.RESET}")
        wait_enter()

    def _run_exercise(self, exercise: Exercise) -> bool:
        """Run a single exercise, return True if correct."""
        print(f"\n{Color.BOLD}{exercise.instruction}{Color.RESET}")

        if exercise.hint:
            print(f"  {Color.DIM}💡 提示: {exercise.hint}{Color.RESET}")

        if exercise.exercise_type == ExerciseType.MULTIPLE_CHOICE:
            return self._run_multiple_choice(exercise)
        elif exercise.exercise_type == ExerciseType.CODE_OUTPUT:
            return self._run_code_output(exercise)
        elif exercise.exercise_type == ExerciseType.FILL_BLANK:
            return self._run_fill_blank(exercise)
        elif exercise.exercise_type == ExerciseType.FREE_PRACTICE:
            return self._run_free_practice(exercise)
        else:
            warning(f"未知练习类型: {exercise.exercise_type}")
            return False

    def _run_multiple_choice(self, exercise: Exercise) -> bool:
        if not exercise.choices:
            error("题目配置错误：缺少选项")
            return False

        for j, choice_text in enumerate(exercise.choices):
            letter = chr(65 + j)  # A, B, C, D
            print(f"  {Color.YELLOW}{letter}.{Color.RESET} {choice_text}")

        answer = prompt("你的答案 (A/B/C/D): ").strip().upper()
        if answer == exercise.correct_answer:
            success("回答正确！")
            return True
        else:
            error(f"回答错误。正确答案是 {exercise.correct_answer}")
            return False

    def _run_code_output(self, exercise: Exercise) -> bool:
        print(f"\n{Color.DIM}请在下方输入你的代码（输入空行结束）：{Color.RESET}")
        user_code_lines = []
        while True:
            line = input("  ")
            if line == "" and user_code_lines:
                break
            if line != "":
                user_code_lines.append(line)

        user_code = "\n".join(user_code_lines)
        if not user_code.strip():
            warning("未输入代码")
            return False

        # Execute user code safely
        code = user_code
        if exercise.setup_code:
            code = exercise.setup_code + "\n" + code

        output = safe_execute(code)
        expected = exercise.expected_output.strip() if exercise.expected_output else ""
        actual = output.strip()

        print(f"\n{Color.DIM}你的输出:{Color.RESET}")
        print(f"  {actual}")
        if exercise.expected_output:
            print(f"\n{Color.DIM}期望输出:{Color.RESET}")
            print(f"  {expected}")

        if actual == expected:
            success("输出正确！")
            return True
        else:
            error("输出不匹配。请检查你的代码。")
            if exercise.reference_answer:
                print(f"\n{Color.DIM}参考答案:{Color.RESET}")
                code_block(exercise.reference_answer)
            return False

    def _run_fill_blank(self, exercise: Exercise) -> bool:
        if not exercise.template:
            error("题目配置错误：缺少模板")
            return False

        print(f"\n{Color.DIM}补全下方代码，将 ___ 替换为正确内容：{Color.RESET}")
        code_block(exercise.template)
        blank_count = exercise.template.count(exercise.blank_marker)
        print(f"{Color.DIM}（共 {blank_count} 处需要填写）{Color.RESET}")

        user_code = []
        if blank_count == 1:
            answer = prompt(f"填写 {exercise.blank_marker} 的内容: ").strip()
            user_code = [exercise.template.replace(exercise.blank_marker, answer)]
        else:
            print(f"\n{Color.DIM}请在下方输入完整的代码（输入空行结束）：{Color.RESET}")
            while True:
                line = input("  ")
                if line == "" and user_code:
                    break
                if line != "":
                    user_code.append(line)

        code = "\n".join(user_code)
        if not code.strip():
            warning("未输入代码")
            return False

        output = safe_execute(code)
        expected = exercise.expected_output.strip() if exercise.expected_output else ""

        print(f"\n{Color.DIM}你的输出:{Color.RESET}  {output.strip()}")
        if expected:
            print(f"{Color.DIM}期望输出:{Color.RESET}  {expected}")

        if output.strip() == expected:
            success("正确！")
            return True
        else:
            error("输出不匹配。")
            if exercise.reference_answer:
                print(f"\n{Color.DIM}提示:{Color.RESET}")
                code_block(exercise.reference_answer)
            return False

    def _run_free_practice(self, exercise: Exercise) -> bool:
        print(f"\n{Color.DIM}这是一个自由练习，请尝试完成。输入代码（空行结束）：{Color.RESET}")
        user_code_lines = []
        while True:
            line = input("  ")
            if line == "" and user_code_lines:
                break
            if line != "":
                user_code_lines.append(line)

        user_code = "\n".join(user_code_lines)
        if not user_code.strip():
            warning("未输入代码")
            return False

        output = safe_execute(user_code)
        print(f"\n{Color.DIM}你的输出:{Color.RESET}")
        print(f"  {output.strip()}")

        if exercise.reference_answer:
            print(f"\n{Color.DIM}参考答案:{Color.RESET}")
            code_block(exercise.reference_answer)

        ok = prompt("你的输出正确吗？(y/n): ").strip().lower()
        return ok.startswith("y")


# ── Safe Code Execution ──────────────────────────────────────────────────────

def safe_execute(code: str, timeout: int = 3) -> str:
    """Execute user code in a restricted environment and return stdout."""
    import io
    import threading

    output_buffer = io.StringIO()

    result = {"output": "", "error": None, "timed_out": False}

    def _safe_input(prompt=""):
        raise RuntimeError("input() 在练习中不可用")

    def _target():
        try:
            exec_globals = {"__builtins__": _build_safe_builtins()}
            exec_locals = {}

            # Redirect print to our buffer
            def _print(*args, **kwargs):
                file = kwargs.get("file", output_buffer)
                kwargs["file"] = file
                print(*args, **kwargs)

            exec_globals["print"] = _print

            compiled = compile(code, "<exercise>", "exec")
            exec(compiled, exec_globals, exec_locals)

            result["output"] = output_buffer.getvalue()
        except Exception as e:
            result["error"] = str(e)
            result["output"] = output_buffer.getvalue()

    def _build_safe_builtins():
        return {
            "print": print,
            "len": len,
            "range": range,
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "set": set,
            "type": type, "isinstance": isinstance,
            "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
            "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
            "sorted": sorted, "reversed": reversed,
            "True": True, "False": False, "None": None,
            "input": _safe_input,
            "Exception": Exception, "ValueError": ValueError,
            "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError,
        }

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        result["timed_out"] = True
        result["error"] = "代码执行超时（超过3秒）"

    if result["error"]:
        return f"[错误] {result['error']}"
    return result["output"]
