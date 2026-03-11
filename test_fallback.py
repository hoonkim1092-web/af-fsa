def divide_numbers(a, b):
    # Intentional logical error for simulation
    return a / b

if __name__ == "__main__":
    print("Running data processing...")
    # This will cause a ZeroDivisionError
    result = divide_numbers(10, 0)
    print(f"Result is {result}")
