최고급 일식당의 마스터 셰프님을 위한 '계절 오마카세 재고 최적화' CLI 도구입니다. 이 도구는 계절별 식재료 재고를 효율적으로 관리하고, 오마카세 메뉴 구성을 위한 제철 식재료를 추천하며, 재고 현황을 파악하는 데 도움을 줍니다. 모든 닥스트링과 출력 메시지는 한국어로 작성되었습니다.

---


import argparse
import json
import os
from datetime import datetime
from collections import defaultdict

# --- 설정 (환경에 따라 변경 가능) ---
INVENTORY_FILE = 'seasonal_omakase_inventory.json'
LOW_STOCK_THRESHOLD = 50  # 단위에 따라 유동적 (예: 50g, 50개)

# 계절 정의 (월별)
SEASON_MAP = {
    1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄", 6: "여름",
    7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을", 12: "겨울"
}
VALID_SEASONS = ["봄", "여름", "가을", "겨울", "사계절"]

# --- 유틸리티 함수 ---

def get_current_season(date=None):
    """
    주어진 날짜 또는 현재 날짜에 해당하는 계절을 반환합니다.
    """
    if date is None:
        date = datetime.now()
    return SEASON_MAP.get(date.month, "알 수 없음")

def load_inventory():
    """
    INVENTORY_FILE에서 재고 데이터를 불러옵니다.
    파일이 없거나 JSON 형식이 유효하지 않으면 빈 리스트를 반환합니다.
    """
    if not os.path.exists(INVENTORY_FILE):
        return []
    try:
        with open(INVENTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"오류: '{INVENTORY_FILE}' 파일이 올바른 JSON 형식이 아닙니다. 새 파일로 시작합니다.")
        return []
    except Exception as e:
        print(f"파일을 읽는 중 오류가 발생했습니다: {e}")
        return []

def save_inventory(inventory):
    """
    재고 데이터를 INVENTORY_FILE에 저장합니다.
    """
    try:
        with open(INVENTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(inventory, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"파일을 저장하는 중 오류가 발생했습니다: {e}")

def find_ingredient(inventory, name):
    """
    재고 목록에서 주어진 이름의 재료를 찾아 인덱스와 함께 반환합니다.
    """
    for i, item in enumerate(inventory):
        if item['name'].lower() == name.lower():
            return i, item
    return None, None

def format_currency(amount):
    """
    금액을 통화 형식으로 포맷합니다 (예: 1,234원).
    """
    return f"{int(amount):,}원"

# --- CLI 명령어 구현 ---

def add_ingredient(args):
    """
    새로운 식재료를 재고에 추가합니다.
    """
    inventory = load_inventory()

    if find_ingredient(inventory, args.name)[0] is not None:
        print(f"오류: '{args.name}'은(는) 이미 재고에 있습니다. 업데이트 기능을 사용해주세요.")
        return

    if not isinstance(args.quantity, (int, float)) or args.quantity <= 0:
        print("오류: 수량은 0보다 큰 숫자여야 합니다.")
        return

    if not isinstance(args.cost_per_unit, (int, float)) or args.cost_per_unit < 0:
        print("오류: 단위당 원가는 0 이상이어야 합니다.")
        return

    # 계절 입력 처리
    input_seasons = [s.strip() for s in args.season.split(',') if s.strip()]
    invalid_seasons = [s for s in input_seasons if s not in VALID_SEASONS]
    if invalid_seasons:
        print(f"오류: 유효하지 않은 계절이 포함되어 있습니다: {', '.join(invalid_seasons)}")
        print(f"유효한 계절은 {', '.join(VALID_SEASONS)} 입니다.")
        return

    new_ingredient = {
        "name": args.name,
        "quantity": args.quantity,
        "unit": args.unit,
        "supplier": args.supplier,
        "cost_per_unit": args.cost_per_unit,
        "season": input_seasons,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    inventory.append(new_ingredient)
    save_inventory(inventory)
    print(f"'{args.name}' 재료가 성공적으로 추가되었습니다.")

def update_ingredient(args):
    """
    기존 식재료의 수량을 업데이트합니다.
    """
    inventory = load_inventory()
    idx, ingredient = find_ingredient(inventory, args.name)

    if idx is None:
        print(f"오류: '{args.name}' 재료를 찾을 수 없습니다. 'add' 명령으로 추가해주세요.")
        return

    try:
        # +/- 기호를 통한 증감 처리
        if args.quantity_change.startswith('+'):
            change_amount = float(args.quantity_change[1:])
            ingredient['quantity'] += change_amount
            print(f"'{args.name}' 수량이 {change_amount}{ingredient['unit']}만큼 증가했습니다.")
        elif args.quantity_change.startswith('-'):
            change_amount = float(args.quantity_change[1:])
            if ingredient['quantity'] - change_amount < 0:
                print(f"경고: '{args.name}' 재료의 수량이 {ingredient['quantity']}{ingredient['unit']} 밖에 없어, 요청하신 {change_amount}{ingredient['unit']}만큼 감소시키면 음수가 됩니다.")
                confirm = input("그래도 진행하시겠습니까? (y/n): ")
                if confirm.lower() != 'y':
                    print("업데이트를 취소합니다.")
                    return
            ingredient['quantity'] -= change_amount
            print(f"'{args.name}' 수량이 {change_amount}{ingredient['unit']}만큼 감소했습니다.")
        else: # 절대값으로 설정
            new_quantity = float(args.quantity_change)
            if new_quantity < 0:
                print("오류: 수량은 음수가 될 수 없습니다.")
                return
            ingredient['quantity'] = new_quantity
            print(f"'{args.name}' 수량이 {new_quantity}{ingredient['unit']}으로 설정되었습니다.")

        ingredient['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_inventory(inventory)
        print(f"현재 '{args.name}' 재고: {ingredient['quantity']}{ingredient['unit']}")

    except ValueError:
        print("오류: 수량 변경 값은 유효한 숫자여야 합니다 (예: '+100', '-50', '200').")
    except Exception as e:
        print(f"수량 업데이트 중 오류가 발생했습니다: {e}")


def remove_ingredient(args):
    """
    재고에서 식재료를 제거합니다.
    """
    inventory = load_inventory()
    idx, ingredient = find_ingredient(inventory, args.name)

    if idx is None:
        print(f"오류: '{args.name}' 재료를 찾을 수 없습니다.")
        return

    confirm = input(f"정말로 '{args.name}' 재료를 재고에서 삭제하시겠습니까? (y/n): ")
    if confirm.lower() == 'y':
        del inventory[idx]
        save_inventory(inventory)
        print(f"'{args.name}' 재료가 성공적으로 삭제되었습니다.")
    else:
        print("재료 삭제를 취소했습니다.")

def list_inventory(args):
    """
    현재 재고 목록을 다양한 필터를 적용하여 출력합니다.
    """
    inventory = load_inventory()
    
    if not inventory:
        print("재고가 비어있습니다. 'add' 명령으로 재료를 추가해주세요.")
        return

    filtered_inventory = []
    current_season = get_current_season()

    for item in inventory:
        keep = True
        
        # 이름 필터
        if args.name and args.name.lower() not in item['name'].lower():
            keep = False
        
        # 계절 필터
        if args.season:
            target_seasons = [s.strip() for s in args.season.split(',') if s.strip()]
            if not any(s in item['season'] for s in target_seasons):
                keep = False
        elif args.current_season and current_season not in item['season'] and "사계절" not in item['season']:
            keep = False

        # 공급자 필터
        if args.supplier and args.supplier.lower() not in item['supplier'].lower():
            keep = False
        
        # 부족 재료 필터
        if args.low_stock and item['quantity'] > LOW_STOCK_THRESHOLD:
            keep = False
        
        if keep:
            filtered_inventory.append(item)

    if not filtered_inventory:
        print("적용된 필터에 해당하는 재료가 없습니다.")
        return

    # 정렬
    if args.sort_by == 'name':
        filtered_inventory.sort(key=lambda x: x['name'])
    elif args.sort_by == 'quantity':
        filtered_inventory.sort(key=lambda x: x['quantity'])
    elif args.sort_by == 'cost':
        filtered_inventory.sort(key=lambda x: x['cost_per_unit'])
    elif args.sort_by == 'last_updated':
        filtered_inventory.sort(key=lambda x: x['last_updated'], reverse=True)


    print("\n--- 현재 재고 목록 ---")
    print(f"{'이름':<15} {'수량':<10} {'단위':<5} {'단위당 원가':<10} {'총 원가':<10} {'공급자':<15} {'계절':<15} {'최종 업데이트':<20}")
    print("-" * 110)
    total_inventory_cost = 0
    for item in filtered_inventory:
        total_item_cost = item['quantity'] * item['cost_per_unit']
        total_inventory_cost += total_item_cost
        print(f"{item['name']:<15} {item['quantity']:<10.2f} {item['unit']:<5} {format_currency(item['cost_per_unit']):<10} {format_currency(total_item_cost):<10} {item['supplier']:<15} {', '.join(item['season']):<15} {item['last_updated']:<20}")
    print("-" * 110)
    print(f"총 재고 가치: {format_currency(total_inventory_cost)}")
    if args.low_stock and not filtered_inventory:
        print(f"현재 부족한 재료가 없습니다 (기준: {LOW_STOCK_THRESHOLD}).")


def plan_omakase(args):
    """
    현재 재고와 계절을 기반으로 오마카세 메뉴를 위한 식재료를 추천합니다.
    """
    inventory = load_inventory()
    
    if not inventory:
        print("재고가 비어있어 오마카세 메뉴를 계획할 수 없습니다. 'add' 명령으로 재료를 추가해주세요.")
        return

    try:
        if args.date:
            target_date = datetime.strptime(args.date, "%Y-%m-%d")
        else:
            target_date = datetime.now()
        
        target_season = get_current_season(target_date)
    except ValueError:
        print("오류: 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력해주세요.")
        return

    seasonal_ingredients = []
    for item in inventory:
        # 현재 계절이거나 사계절 재료이면서, 재고가 있는 재료만 선택
        if (target_season in item['season'] or "사계절" in item['season']) and item['quantity'] > 0:
            seasonal_ingredients.append(item)

    if not seasonal_ingredients:
        print(f"'{target_date.strftime('%Y년 %m월 %d일')}' ({target_season})에 사용 가능한 제철 재료가 없습니다.")
        print("재고를 확인하거나 다른 계절 재료를 추가해보세요.")
        return

    # 추천 로직 (예: 신선도(업데이트일 기준), 재고량, 원가 등을 복합적으로 고려)
    # 여기서는 "단위당 원가가 낮은 순"으로 정렬하되, "재고가 많은" 것을 우선적으로 고려
    # 더 복잡한 알고리즘(예: 선입선출, 유통기한 임박 등)은 데이터 모델 확장 필요
    seasonal_ingredients.sort(key=lambda x: (x['cost_per_unit'], -x['quantity'])) 

    print(f"\n--- {target_date.strftime('%Y년 %m월 %d일')} ({target_season}) 오마카세 추천 재료 ---")
    print(f"{'이름':<20} {'수량':<10} {'단위':<5} {'단위당 원가':<10} {'총 예상 원가':<15} {'공급자':<15} {'계절':<10}")
    print("-" * 90)

    recommended_count = 0
    total_estimated_cost = 0
    
    # 추천할 재료 개수 제한 (기본값 5개, --count 옵션으로 변경 가능)
    for item in seasonal_ingredients[:args.count]:
        # 오마카세 구성에 필요한 재료량은 임의로 1단위로 가정
        # 실제로는 각 요리에 필요한 재료량에 따라 달라질 수 있음
        estimated_use_quantity = 1 if item['unit'] == '개' else 100 # 예: 개수는 1개, 그램은 100g
        
        # 실제 재고량보다 많이 추천하지 않도록 조정
        actual_use_quantity = min(estimated_use_quantity, item['quantity']) 
        
        if actual_use_quantity == 0:
            continue # 재고가 없으면 추천하지 않음

        item_cost = actual_use_quantity * item['cost_per_unit']
        total_estimated_cost += item_cost
        
        print(f"{item['name']:<20} {actual_use_quantity:<10.2f} {item['unit']:<5} {format_currency(item['cost_per_unit']):<10} {format_currency(item_cost):<15} {item['supplier']:<15} {', '.join(item['season']):<10}")
        recommended_count += 1

    if recommended_count == 0:
        print(f"'{target_date.strftime('%Y년 %m월 %d일')}' ({target_season})에 충분한 재고를 가진 제철 재료가 없습니다.")
    else:
        print("-" * 90)
        print(f"총 예상 재료 원가: {format_currency(total_estimated_cost)}")
        print("\n* 위 추천은 재고량과 단위당 원가를 기반으로 합니다. 최종 선택은 마스터 셰프님의 판단에 따라 주세요.")

# --- 메인 파서 설정 ---

def main():
    """
    CLI 도구의 메인 함수입니다. argparse를 설정하고 명령어를 처리합니다.
    """
    parser = argparse.ArgumentParser(
        description="""
        최고급 일식당 마스터 셰프를 위한 계절 오마카세 재고 최적화 도구.
        계절별 식재료 재고를 관리하고 오마카세 메뉴 구성을 위한 제철 식재료를 추천합니다.
        """,
        formatter_class=argparse.RawTextHelpFormatter # 줄바꿈 유지를 위함
    )

    subparsers = parser.add_subparsers(dest='command', help='사용 가능한 명령어')

    # 'add' 명령어
    add_parser = subparsers.add_parser('add', help='새로운 식재료를 재고에 추가합니다.')
    add_parser.add_argument('name', type=str, help='추가할 식재료의 이름')
    add_parser.add_argument('quantity', type=float, help='식재료의 초기 수량')
    add_parser.add_argument('unit', type=str, help='식재료 수량의 단위 (예: kg, g, 개, 마리)')
    add_parser.add_argument('supplier', type=str, help='식재료의 공급자')
    add_parser.add_argument('cost_per_unit', type=float, help='단위당 원가 (예: 100g당 5000원)')
    add_parser.add_argument('--season', type=str, default="사계절",
                            help=f"식재료의 제철 계절 (쉼표로 구분). 유효한 값: {', '.join(VALID_SEASONS)} (기본값: 사계절)")
    add_parser.set_defaults(func=add_ingredient)

    # 'update' 명령어
    update_parser = subparsers.add_parser('update', help='기존 식재료의 수량을 업데이트합니다. (예: "+100", "-50", "200")')
    update_parser.add_argument('name', type=str, help='수량을 업데이트할 식재료의 이름')
    update_parser.add_argument('quantity_change', type=str,
                               help='수량 변경 값. "+숫자"는 추가, "-숫자"는 감소, "숫자"는 해당 값으로 설정합니다.')
    update_parser.set_defaults(func=update_ingredient)

    # 'remove' 명령어
    remove_parser = subparsers.add_parser('remove', help='재고에서 식재료를 제거합니다.')
    remove_parser.add_argument('name', type=str, help='제거할 식재료의 이름')
    remove_parser.set_defaults(func=remove_ingredient)

    # 'list' 명령어
    list_parser = subparsers.add_parser('list', help='현재 재고 목록을 출력합니다.')
    list_parser.add_argument('--name', type=str, help='특정 이름의 재료만 필터링')
    list_parser.add_argument('--season', type=str,
                             help=f"특정 계절의 재료만 필터링 (쉼표로 구분). 유효한 값: {', '.join(VALID_SEASONS)}")
    list_parser.add_argument('--current-season', action='store_true',
                             help='현재 계절 (또는 사계절)에 맞는 재료만 필터링')
    list_parser.add_argument('--supplier', type=str, help='특정 공급자의 재료만 필터링')
    list_parser.add_argument('--low-stock', action='store_true',
                             help=f'재고 부족 알림 ({LOW_STOCK_THRESHOLD} {{"단위"}}) 재료만 필터링')
    list_parser.add_argument('--sort-by', type=str, choices=['name', 'quantity', 'cost', 'last_updated'], default='name',
                             help='정렬 기준 (name, quantity, cost, last_updated)')
    list_parser.set_defaults(func=list_inventory)

    # 'plan' 명령어
    plan_parser = subparsers.add_parser('plan', help='계절에 맞는 오마카세 추천 식재료 목록을 생성합니다.')
    plan_parser.add_argument('--date', type=str,
                             help='계획을 세울 날짜 (YYYY-MM-DD 형식). 기본값은 오늘 날짜입니다.')
    plan_parser.add_argument('--count', type=int, default=5,
                             help='추천할 재료의 최대 개수 (기본값: 5)')
    plan_parser.set_defaults(func=plan_omakase)


    args = parser.parse_args()

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()


---

### 사용 방법 (터미널)

1.  **파일 저장**: 위 코드를 `seasonal_omakase_inventory_optimization.py` 파일로 저장합니다.
2.  **초기 실행**: `./seasonal_omakase_inventory_optimization.py`
    *   아무런 인자 없이 실행하면 도움말 메시지가 출력됩니다.

3.  **식재료 추가**:
    bash
    python seasonal_omakase_inventory_optimization.py add "참치 아카미" 1000 g "츠키지 시장" 50 --season "겨울,봄"
    python seasonal_omakase_inventory_optimization.py add "제주 갈치" 5 개 "한라 수산" 15000 --season "여름,가을"
    python seasonal_omakase_inventory_optimization.py add "고등어" 8 마리 "남해수산" 8000 --season "가을,겨울"
    python seasonal_omakase_inventory_optimization.py add "쌀 (고시히카리)" 20000 g "평창 농협" 10 --season "사계절"
    python seasonal_omakase_inventory_optimization.py add "와사비" 500 g "지리산농원" 100 --season "사계절"
    python seasonal_omakase_inventory_optimization.py add "단새우" 30 마리 "동해수산" 1200 --season "겨울"
    

4.  **재고 목록 확인**:
    *   모든 재고:
        bash
        python seasonal_omakase_inventory_optimization.py list
        
    *   현재 계절 재료만:
        bash
        python seasonal_omakase_inventory_optimization.py list --current-season
        
    *   부족한 재고만:
        bash
        python seasonal_omakase_inventory_optimization.py list --low-stock
        
    *   특정 공급자의 재료만:
        bash
        python seasonal_omakase_inventory_optimization.py list --supplier "츠키지 시장"
        
    *   이름으로 검색:
        bash
        python seasonal_omakase_inventory_optimization.py list --name "참치"
        
    *   가을 재료만:
        bash
        python seasonal_omakase_inventory_optimization.py list --season "가을"
        
    *   수량 기준으로 정렬:
        bash
        python seasonal_omakase_inventory_optimization.py list --sort-by quantity
        

5.  **재고 수량 업데이트**:
    *   "참치 아카미" 500g 추가:
        bash
        python seasonal_omakase_inventory_optimization.py update "참치 아카미" "+500"
        
    *   "제주 갈치" 2개 사용:
        bash
        python seasonal_omakase_inventory_optimization.py update "제주 갈치" "-2"
        
    *   "고등어" 재고를 10마리로 설정:
        bash
        python seasonal_omakase_inventory_optimization.py update "고등어" "10"
        

6.  **식재료 제거**:
    bash
    python seasonal_omakase_inventory_optimization.py remove "와사비"
    

7.  **오마카세 메뉴 계획**:
    *   오늘 날짜 기준으로 추천:
        bash
        python seasonal_omakase_inventory_optimization.py plan
        
    *   특정 날짜 기준으로 추천 (예: 2024년 7월 15일):
        bash
        python seasonal_omakase_inventory_optimization.py plan --date "2024-07-15"
        
    *   추천 재료 갯수 지정:
        bash
        python seasonal_omakase_inventory_optimization.py plan --count 3
        

이 도구는 `seasonal_omakase_inventory.json` 파일을 생성하여 재고 데이터를 저장하고 관리합니다.