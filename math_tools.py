def add(a: float, b: float) -> float:
    """دالة لحساب حاصل جمع عددين."""
    print(f"Adding {a} + {b}")
    return a + b


def multiply(a: float, b: float) -> float:
    """دالة لحساب حاصل ضرب عددين."""
    return a * b


def factorial(n: int) -> int:
    """دالة لحساب مضروب (Factorial) عدد صحيح موجب."""
    if n < 0:
        raise ValueError("لا يمكن حساب مضروب الأعداد السالبة.")
    if n in (0, 1):
        return 1

    result = 1
    for i in range(2, n + 1):
        result *= i
    return result