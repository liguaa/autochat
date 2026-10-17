"""Store the DeepSeek API key in Windows Credential Manager."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import winreg


SERVICE = "autochat.deepseek"
TYPESAFE_SERVICE = "autochat.typesafe"
USERNAME = "api-key"
USER_ENV = r"Environment"
MACHINE_ENV = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def _registry_value(root, path: str, name: str) -> str:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
            return value.strip() if isinstance(value, str) else ""
    except OSError:
        return ""


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("请先安装 keyring 和 pywin32，或设置 DEEPSEEK_API_KEY") from exc
    return keyring


def get_api_key() -> str:
    environment = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if environment:
        return environment
    for root, path in ((winreg.HKEY_CURRENT_USER, USER_ENV), (winreg.HKEY_LOCAL_MACHINE, MACHINE_ENV)):
        environment = _registry_value(root, path, "DEEPSEEK_API_KEY")
        if environment:
            return environment
    try:
        import keyring
    except ImportError:
        return ""
    try:
        return (keyring.get_password(SERVICE, USERNAME) or "").strip()
    except Exception as exc:
        raise RuntimeError("无法读取 Windows 凭据管理器中的 DeepSeek 密钥") from exc


def get_typesafe_api_key() -> str:
    environment = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if environment:
        return environment
    for root, path in ((winreg.HKEY_CURRENT_USER, USER_ENV), (winreg.HKEY_LOCAL_MACHINE, MACHINE_ENV)):
        environment = _registry_value(root, path, "TYPESAFE_API_KEY")
        if environment:
            return environment
    try:
        import keyring
    except ImportError:
        return ""
    try:
        return (keyring.get_password(TYPESAFE_SERVICE, USERNAME) or "").strip()
    except Exception as exc:
        raise RuntimeError("无法读取 Windows 凭据管理器中的 TypeSafe 密钥") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["set", "set-env", "status", "delete", "delete-env"])
    parser.add_argument("--provider", choices=["deepseek", "typesafe"], default="deepseek")
    args = parser.parse_args(argv)
    service = SERVICE if args.provider == "deepseek" else TYPESAFE_SERVICE
    env_name = "DEEPSEEK_API_KEY" if args.provider == "deepseek" else "TYPESAFE_API_KEY"
    label = "DeepSeek" if args.provider == "deepseek" else "TypeSafe"
    try:
        if args.action == "set":
            value = getpass.getpass(f"{label} API key: ").strip()
            if not value:
                raise ValueError("密钥不能为空")
            _keyring().set_password(service, USERNAME, value)
            print("已保存到 Windows 凭据管理器；不会输出密钥。")
        elif args.action == "set-env":
            value = getpass.getpass(f"{label} API key: ").strip()
            if not value:
                raise ValueError("密钥不能为空")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, USER_ENV, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.SetValueEx(handle, env_name, 0, winreg.REG_SZ, value)
            print(f"已保存为 Windows 用户环境变量 {env_name}；不会输出密钥。")
        elif args.action == "status":
            print("已配置" if (get_api_key() if args.provider == "deepseek" else get_typesafe_api_key()) else "未配置")
        elif args.action == "delete":
            _keyring().delete_password(service, USERNAME)
            print("已删除凭据管理器中的密钥。")
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_ENV, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, env_name)
            print("已删除 Windows 用户环境变量中的密钥。")
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
