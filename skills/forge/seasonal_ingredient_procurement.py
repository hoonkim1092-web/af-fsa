#!/usr/bin/env python3
"""
일식 마스터 셰프를 위한 제철 식재료 발주 및 관리 CLI 도구입니다.
계절별 최상급 식재료를 확인하고, 그날의 오마카세를 위한 발주를 진행하며,
과거 발주 내역을 관리할 수 있습니다.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Any

# 발주 내역을 저장할 파일 경로
HISTORY_FILE = "procurement_history.json"

# 일식 기반 계절별 제철 식재료 데이터
SEASONAL_INGREDIENTS = {
    "봄": ["참돔(마다이)", "도다리(메이타가레이)", "주꾸미", "두릅", "벚굴(사쿠라가키)"],
    "여름": ["갯장어(하모)", "민어", "농어(스즈키)", "성게알(우니)", "전복(아와비)"],
    "가을": ["전어(고하다)", "자연산 송이버섯", "고등어(사바)", "꽁치(산마)", "꽃게"],
    "겨울": ["대방어(부리)", "참복(토라후구)", "대게(즈와이가니)", "굴(카키)", "물메기"]
}


def load_history() -> List[Dict[str, Any]]:
    """
    저장된 발주 내역을 불러옵니다.

    Returns:
        List[Dict[str, Any]]: 발주 내역 리스트. 파일이 없거나 손상된 경우 빈 리스트 반환.
    """
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[오류] 발주 내역 파일이 손상되었습니다. 새로운 내역으로 시작합니다.")
        return []
    except Exception as e:
        print(f"[오류] 파일을 읽는 중 문제가 발생했습니다: {e}")
        return []


def save_history(history: List[Dict[str, Any]]) -> None:
    """
    발주 내역을 파일에 저장합니다.

    Args:
        history (List[Dict[str, Any]]): 저장할 발주 내역 리스트.
    """
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[오류] 발주 내역을 저장하는 데 실패했습니다: {e}")


def handle_recommend(args: argparse.Namespace) -> None:
    """
    계절에 맞는 일식 제철 식재료를 추천하여 출력합니다.

    Args:
        args (argparse.Namespace): 사용자가 입력한 인자 객체.
    """
    season = args.season
    ingredients = SEASONAL_INGREDIENTS.get(season)

    if not ingredients:
        print(f"[오류] 알 수 없는 계절입니다: '{season}'. (봄, 여름, 가을, 겨울 중 선택)")
        sys.exit(1)

    icons = {"봄": "🌸", "여름": "🌊", "가을": "🍁", "겨울": "❄️"}
    icon = icons.get(season, "🍣")

    print(f"\n=== {icon} {season} 제철 최상급 식재료 추천 목록 ===")
    for idx, item in enumerate(ingredients, 1):
        print(f" {idx}. {item}")
    print("===========================================\n")
    print("오마카세 메뉴 구성에 참고하시기 바랍니다.\n")


def handle_order(args: argparse.Namespace) -> None:
    """
    식재료 발주를 진행하고 내역에 저장합니다.

    Args:
        args (argparse.Namespace): 사용자가 입력한 인자 객체.
    """
    ingredient = args.ingredient
    quantity = args.quantity
    unit = args.unit
    
    if quantity <= 0:
        print("[오류] 수량은 0보다 커야 합니다.")
        sys.exit(1)

    history = load_history()
    
    order_record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ingredient": ingredient,
        "quantity": quantity,
        "unit": unit
    }
    
    history.append(order_record)
    save_history(history)
    
    print(f"\n[발주 완료] 정성껏 준비하겠습니다.")
    print(f" - 품목: {ingredient}")
    print(f" - 수량: {quantity} {unit}")
    print(f" - 일시: {order_record['timestamp']}\n")


def handle_history(args: argparse.Namespace) -> None:
    """
    지금까지의 발주 내역을 출력합니다.

    Args:
        args (argparse.Namespace): 사용자가 입력한 인자 객체.
    """
    history = load_history()
    
    if not history:
        print("\n[알림] 현재 저장된 발주 내역이 없습니다.\n")
        return

    print("\n=== 📜 식재료 발주 내역 ===")
    for record in history:
        date = record.get("timestamp", "날짜 없음")
        item = record.get("ingredient", "알 수 없음")
        qty = record.get("quantity", 0)
        unit = record.get("unit", "")
        print(f"[{date}] {item} - {qty}{unit}")
    print("===========================\n")


def main() -> None:
    """
    메인 진입점입니다. 인자를 파싱하고 적절한 서브커맨드 함수를 호출합니다.
    """
    parser = argparse.ArgumentParser(
        description="일식 마스터 셰프를 위한 제철 식재료 조달 및 관리 시스템",
        formatter_class=argparse.RawTextHelpFormatter
    )

    subparsers = parser.add_subparsers(title="명령어", dest="command", required=True)

    # 1. 추천 (recommend) 명령어
    parser_recommend = subparsers.add_parser(
        "recommend", 
        help="특정 계절의 일식 제철 식재료를 추천받습니다."
    )
    parser_recommend.add_argument(
        "season", 
        choices=["봄", "여름", "가을", "겨울"], 
        help="조회할 계절 (예: 봄)"
    )
    parser_recommend.set_defaults(func=handle_recommend)

    # 2. 발주 (order) 명령어
    parser_order = subparsers.add_parser(
        "order", 
        help="식재료를 발주합니다."
    )
    parser_order.add_argument(
        "ingredient", 
        type=str, 
        help="발주할 식재료명 (예: 참돔)"
    )
    parser_order.add_argument(
        "-q", "--quantity", 
        type=float, 
        default=1.0, 
        help="발주 수량 (기본값: 1.0)"
    )
    parser_order.add_argument(
        "-u", "--unit", 
        type=str, 
        default="kg", 
        help="수량 단위 (예: kg, 마리, Box 등. 기본값: kg)"
    )
    parser_order.set_defaults(func=handle_order)

    # 3. 내역 조회 (history) 명령어
    parser_history = subparsers.add_parser(
        "history", 
        help="과거 발주 내역을 조회합니다."
    )
    parser_history.set_defaults(func=handle_history)

    # 인자 파싱 및 실행
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()