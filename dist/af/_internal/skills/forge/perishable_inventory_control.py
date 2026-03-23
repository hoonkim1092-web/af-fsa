#!/usr/bin/env python3
"""
일식당 마스터 셰프를 위한 신선 식재료 재고 관리 CLI 도구
식재료의 수량, 단위, 유통기한을 기록하고 선도를 관리합니다.
"""

import argparse
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any

INVENTORY_FILE = 'perishable_inventory.json'


def load_inventory() -> Dict[str, Any]:
    """
    JSON 파일에서 식재료 재고 데이터를 불러옵니다.
    파일이 존재하지 않으면 빈 딕셔너리를 반환합니다.
    """
    if not os.path.exists(INVENTORY_FILE):
        return {}
    try:
        with open(INVENTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("경고: 재고 파일이 손상되었습니다. 빈 재고로 시작합니다.")
        return {}


def save_inventory(data: Dict[str, Any]) -> None:
    """
    식재료 재고 데이터를 JSON 파일에 저장합니다.
    """
    with open(INVENTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def add_item(args: argparse.Namespace) -> None:
    """
    새로운 신선 식재료를 재고에 추가합니다.
    """
    try:
        # 날짜 형식 검증
        datetime.strptime(args.expiry, "%Y-%m-%d")
    except ValueError:
        print("오류: 유통기한은 YYYY-MM-DD 형식이어야 합니다. (예: 2023-12-31)")
        return

    inventory = load_inventory()
    inventory[args.name] = {
        "quantity": args.quantity,
        "unit": args.unit,
        "expiry_date": args.expiry
    }
    save_inventory(inventory)
    print(f"성공: [{args.name}] 식재료가 입고되었습니다. (수량: {args.quantity}{args.unit}, 유통기한: {args.expiry})")


def update_item(args: argparse.Namespace) -> None:
    """
    기존 식재료의 수량을 업데이트합니다.
    """
    inventory = load_inventory()
    if args.name not in inventory:
        print(f"오류: 재고 목록에 '{args.name}'이(가) 존재하지 않습니다.")
        return

    inventory[args.name]["quantity"] = args.quantity
    save_inventory(inventory)
    unit = inventory[args.name]["unit"]
    print(f"성공: [{args.name}]의 남은 수량이 {args.quantity}{unit}(으)로 갱신되었습니다.")


def remove_item(args: argparse.Namespace) -> None:
    """
    소진되었거나 폐기된 식재료를 재고에서 완전히 삭제합니다.
    """
    inventory = load_inventory()
    if args.name in inventory:
        del inventory[args.name]
        save_inventory(inventory)
        print(f"성공: [{args.name}] 식재료가 재고에서 삭제되었습니다.")
    else:
        print(f"오류: 재고 목록에 '{args.name}'이(가) 존재하지 않습니다.")


def list_items(args: argparse.Namespace) -> None:
    """
    현재 보관 중인 모든 식재료 목록을 유통기한 임박 순으로 정렬하여 출력합니다.
    """
    inventory = load_inventory()
    if not inventory:
        print("현재 보관 중인 식재료가 없습니다.")
        return

    print("\n=== [일식당 마스터 셰프 신선 식재료 재고 현황] ===")
    
    # 유통기한(오름차순)으로 정렬
    sorted_items = sorted(inventory.items(), key=lambda x: x[1]['expiry_date'])
    
    for name, info in sorted_items:
        print(f" 🍣 {name}: {info['quantity']}{info['unit']} (유통기한: {info['expiry_date']})")
    print("==================================================\n")


def alert_items(args: argparse.Namespace) -> None:
    """
    폐기 임박(사용자가 지정한 일수 이내) 또는 이미 유통기한이 지난 식재료를 확인합니다.
    일식의 생명인 선도 관리를 위한 기능입니다.
    """
    inventory = load_inventory()
    if not inventory:
        print("현재 보관 중인 식재료가 없습니다.")
        return

    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    alert_timedelta = timedelta(days=args.days)

    print(f"\n=== [선도 경고 리포트 (유통기한 {args.days}일 이내)] ===")
    found = False
    sorted_items = sorted(inventory.items(), key=lambda x: x[1]['expiry_date'])

    for name, info in sorted_items:
        expiry_date = datetime.strptime(info['expiry_date'], "%Y-%m-%d")
        time_left = expiry_date - today

        if time_left.days < 0:
            print(f" ❌ [즉시 폐기 요망] {name}: {info['quantity']}{info['unit']} (유통기한 만료일: {info['expiry_date']})")
            found = True
        elif time_left <= alert_timedelta:
            print(f" ⚠️ [빠른 소진 요망] {name}: {info['quantity']}{info['unit']} (유통기한: {info['expiry_date']}, {time_left.days}일 남음)")
            found = True

    if not found:
        print(" 경고 대상인 식재료가 없습니다. 모든 식재료가 훌륭한 선도를 유지하고 있습니다!")
    print("==================================================\n")


def main() -> None:
    """
    명령줄 인자를 파싱하고 적절한 함수로 라우팅합니다.
    """
    parser = argparse.ArgumentParser(
        description="일식당 마스터 셰프를 위한 완벽한 신선 식재료 재고 관리 시스템"
    )
    subparsers = parser.add_subparsers(title="명령어", dest="command", required=True)

    # 1. 추가(add) 명령어
    parser_add = subparsers.add_parser('add', help="새로운 식재료를 입고합니다.")
    parser_add.add_argument('name', type=str, help="식재료 이름 (예: 생연어, 참다랑어)")
    parser_add.add_argument('quantity', type=float, help="입고 수량")
    parser_add.add_argument('unit', type=str, help="수량 단위 (예: kg, g, 마리, EA)")
    parser_add.add_argument('expiry', type=str, help="유통기한 (YYYY-MM-DD 형식)")
    parser_add.set_defaults(func=add_item)

    # 2. 갱신(update) 명령어
    parser_update = subparsers.add_parser('update', help="기존 식재료의 남은 수량을 갱신합니다.")
    parser_update.add_argument('name', type=str, help="식재료 이름")
    parser_update.add_argument('quantity', type=float, help="현재 남은 수량")
    parser_update.set_defaults(func=update_item)

    # 3. 삭제(remove) 명령어
    parser_remove = subparsers.add_parser('remove', help="소진되거나 폐기된 식재료를 목록에서 삭제합니다.")
    parser_remove.add_argument('name', type=str, help="삭제할 식재료 이름")
    parser_remove.set_defaults(func=remove_item)

    # 4. 조회(list) 명령어
    parser_list = subparsers.add_parser('list', help="전체 식재료 재고를 확인합니다.")
    parser_list.set_defaults(func=list_items)

    # 5. 경고(alert) 명령어
    parser_alert = subparsers.add_parser('alert', help="유통기한이 임박한 식재료를 확인합니다.")
    parser_alert.add_argument('--days', type=int, default=2, help="경고 기준 일수 (기본값: 2일)")
    parser_alert.set_defaults(func=alert_items)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()