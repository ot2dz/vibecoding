def multiply(a: int | float, b: int | float) -> int | float:
    """دالة لضرب رقمين وإرجاع الناتج."""
    return a * b


def test_add():
    """اختبار بسيط لدالة الجمع."""
    assert add(2, 3) == 5, "خطأ: 2 + 3 يجب أن تساوي 5"
    assert add(-1, 1) == 0, "خطأ: -1 + 1 يجب أن تساوي 0"
    assert add(0, 0) == 0, "خطأ: 0 + 0 يجب أن تساوي 0"
    assert add(2.5, 3.5) == 6.0, "خطأ: 2.5 + 3.5 يجب أن تساوي 6.0"
    print("تم اجتياز جميع الاختبارات بنجاح!")


if __name__ == "__main__":
    test_add()