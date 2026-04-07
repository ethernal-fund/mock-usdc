#!/usr/bin/env python3
"""
Script para aplicar migraciones de Alembic a Supabase.

Uso:
    python scripts/migrate.py              # aplica todas las migraciones pendientes
    python scripts/migrate.py --check      # verifica si hay migraciones pendientes
    python scripts/migrate.py --downgrade  # revierte la última migración
"""
import asyncio
import os
import sys
import subprocess
import argparse


def check_env():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("❌ DATABASE_URL no está configurada")
        sys.exit(1)
    print(f"✅ DATABASE_URL encontrada")


def run_alembic(command: list[str]) -> int:
    result = subprocess.run(
        ["alembic"] + command,
        capture_output=False,
    )
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Aplica migraciones de Alembic")
    parser.add_argument("--check", action="store_true", help="Solo verifica el estado")
    parser.add_argument("--downgrade", action="store_true", help="Revierte la última migración")
    args = parser.parse_args()

    check_env()

    if args.check:
        print("🔍 Verificando estado de migraciones...")
        code = run_alembic(["current"])
        print("\n📋 Migraciones pendientes:")
        run_alembic(["history", "--indicate-current"])
        sys.exit(code)

    if args.downgrade:
        print("⬇️  Revirtiendo última migración...")
        code = run_alembic(["downgrade", "-1"])
        if code == 0:
            print("✅ Downgrade exitoso")
        else:
            print("❌ Downgrade falló")
        sys.exit(code)

    print("🚀 Aplicando migraciones...")
    code = run_alembic(["upgrade", "head"])
    if code == 0:
        print("✅ Migraciones aplicadas exitosamente")
    else:
        print("❌ Error aplicando migraciones")
    sys.exit(code)


if __name__ == "__main__":
    main()