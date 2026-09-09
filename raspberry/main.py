import sys

from app.main import main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuario.")
    except Exception as error:
        print(f"Erro: {error}", file=sys.stderr)
        sys.exit(1)
