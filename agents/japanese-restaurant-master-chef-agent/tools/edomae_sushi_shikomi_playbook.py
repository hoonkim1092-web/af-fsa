import argparse
import datetime

# --- 데이터 정의 (Data Definitions) ---

# 재료별 기본 레시피 및 단계
# 이 딕셔너리는 각 재료의 준비 과정과 설명을 담고 있습니다.
# 마스터 셰프의 오랜 경험이 담긴 플레이북의 핵심 내용입니다.
RECIPES = {
    "초밥밥": {
        "설명": "초밥에 사용될 최상의 밥을 준비합니다. 윤기 있고 적당한 찰기를 유지해야 합니다.",
        "단계": [
            "쌀 씻기",
            "쌀 불리기 (30분 ~ 1시간)",
            "정량의 물로 밥 짓기 (고슬고슬하게)",
            "초배합 (식초, 설탕, 소금 비율 조절)",
            "밥 식히기 (부채질하여 윤기 유지)"
        ]
    },
    "참치": {
        "설명": "싱싱한 참치를 숙성하여 사시미 및 니기리용으로 준비합니다. 맛의 깊이를 더하는 과정입니다.",
        "단계": [
            "참치 해동 (냉장 해동 또는 미온수 해동)",
            "핏물 제거 및 물기 닦기",
            "숙성 (다시마 또는 소금, 시간 조절)",
            "분할 및 손질 (도로, 아카미, 주도로 등 부위별)"
        ]
    },
    "광어": {
        "설명": "담백한 맛이 일품인 광어를 준비합니다. 숙성을 통해 감칠맛을 끌어올립니다.",
        "단계": [
            "광어 손질 (비늘, 내장 제거, 세척)",
            "포 뜨기 (숙련된 기술 필요)",
            "숙성 (곤부즈메 또는 소금, 1~2일)",
            "껍질 벗기기 및 모양 잡기 (니기리용)"
        ]
    },
    "장어": {
        "설명": "입맛을 돋우는 부드러운 장어를 준비합니다. 특제 소스가 맛의 핵심입니다.",
        "단계": [
            "장어 손질 및 뼈 제거",
            "초벌 굽기 (기름기 제거)",
            "특제 소스 바르기 (비법 간장 소스)",
            "재벌 굽기 (소스가 스며들고 바삭하게)"
        ]
    },
    "새우": {
        "설명": "쫄깃하고 달콤한 새우를 준비합니다. 초밥용으로 적합한 형태로 익혀야 합니다.",
        "단계": [
            "새우 손질 (내장, 껍질 제거)",
            "살짝 삶기 (너무 익지 않게)",
            "펼치기 (에비 니기리용 모양 잡기)"
        ]
    },
    "계란말이": {
        "설명": "부드럽고 촉촉한 일식 계란말이를 만듭니다. 섬세한 불 조절이 중요합니다.",
        "단계": [
            "재료 혼합 (계란, 다시마 육수, 설탕, 간장)",
            "계란말이 팬 예열 및 기름칠",
            "여러 겹으로 말아가며 굽기 (타지 않게)",
            "모양 잡기 및 식히기"
        ]
    },
    "초생강": {
        "설명": "입맛을 돋우는 상큼한 초생강을 준비합니다. 초밥 사이에 먹는 훌륭한 조연입니다.",
        "단계": [
            "생강 세척 및 얇게 썰기",
            "소금에 절여 물기 빼기",
            "단촛물 (식초, 설탕, 소금) 만들기",
            "단촛물에 절이기 (하루 이상 숙성)"
        ]
    },
    "간장": {
        "설명": "초밥과 어울리는 특제 간장을 숙성합니다. 스시의 맛을 더욱 돋보이게 합니다.",
        "단계": [
            "베이스 간장 준비 (좋은 품질의 간장)",
            "다시마, 가쓰오부시 등 추가",
            "은은하게 끓이기 (향을 더함)",
            "식히기 및 숙성 (최소 1주일)"
        ]
    }
}

# 현재 세션의 준비 로그
# 이 리스트는 스크립트 실행 중에 완료된 준비 작업을 기록합니다.
# 실제 운영에서는 데이터베이스나 파일에 저장되어야 할 정보입니다.
PREPARATION_LOG = []

# --- 헬퍼 함수 (Helper Functions) ---

def get_available_ingredients():
    """
    현재 등록된 모든 재료 목록을 반환합니다.
    마스터 셰프의 플레이북에 기록된 모든 재료를 보여줍니다.

    Returns:
        list: 등록된 재료 이름 목록.
    """
    return list(RECIPES.keys())

def find_recipe_steps(ingredient_name):
    """
    주어진 재료에 대한 레시피 단계를 찾아 반환합니다.
    Args:
        ingredient_name (str): 레시피를 찾을 재료의 이름.

    Returns:
        list or None: 재료의 단계 목록 또는 재료를 찾을 수 없는 경우 None.
    """
    return RECIPES.get(ingredient_name, {}).get("단계")

# --- 커맨드 핸들러 함수 (Command Handler Functions) ---

def handle_prepare(args):
    """
    'prepare' 명령을 처리합니다. 특정 재료의 준비 완료를 기록합니다.
    마스터 셰프가 재료 준비를 완료했을 때 이를 플레이북에 기록하는 역할을 합니다.

    Args:
        args (argparse.Namespace): argparse에 의해 파싱된 인자 객체.
    """
    ingredient = args.재료명
    quantity = args.양
    step = args.단계

    if ingredient not in RECIPES:
        print(f"오류: '{ingredient}'(은)는 아직 저의 플레이북에 없는 재료입니다. 'recipe 목록' 명령으로 사용 가능한 재료를 확인해 주세요.")
        return

    recipe_steps = find_recipe_steps(ingredient)
    if step and step not in recipe_steps:
        print(f"오류: '{ingredient}' 재료에 대한 '{step}' 단계는 존재하지 않습니다. 가능한 단계: {', '.join(recipe_steps)}")
        return

    log_entry = {
        "시간": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "재료": ingredient,
        "양": quantity if quantity else "미지정",
        "단계": step if step else "전체",
        "상태": "준비 완료"
    }
    PREPARATION_LOG.append(log_entry)
    print(f"✅ '{ingredient}' ({log_entry['단계']}) 준비 완료를 기록합니다.{f' (양: {quantity})' if quantity else ''}")
    if not step and len(recipe_steps) > 1:
        print(f"💡 '{ingredient}' 재료는 여러 단계로 준비될 수 있습니다. '--단계' 옵션을 사용하여 특정 단계를 기록할 수 있습니다. (예: prepare 초밥밥 --단계 '초배합')")

def handle_status(args):
    """
    'status' 명령을 처리합니다. 현재 준비된 재료들의 상태를 출력합니다.
    마스터 셰프가 현재 주방의 준비 현황을 한눈에 파악할 수 있도록 돕습니다.

    Args:
        args (argparse.Namespace): argparse에 의해 파싱된 인자 객체.
    """
    target_ingredient = args.재료

    if not PREPARATION_LOG:
        print("현재까지 기록된 준비 내역이 없습니다. 'prepare' 명령으로 재료를 준비해 주세요.")
        return

    print("\n--- 🍣 에도마에 스시 시코미 준비 현황 ---")
    found_any = False
    for entry in PREPARATION_LOG:
        if not target_ingredient or entry["재료"] == target_ingredient:
            print(f"[{entry['시간']}] {entry['재료']} - 단계: {entry['단계']}, 양: {entry['양']}, 상태: {entry['상태']}")
            found_any = True

    if target_ingredient and not found_any:
        print(f"'{target_ingredient}'에 대한 준비 내역은 아직 없습니다.")
    print("------------------------------------------\n")


def handle_recipe(args):
    """
    'recipe' 명령을 처리합니다. 특정 재료의 레시피 단계를 출력합니다.
    마스터 셰프가 직접 작성한 재료별 준비 노하우를 상세히 알려줍니다.

    Args:
        args (argparse.Namespace): argparse에 의해 파싱된 인자 객체.
    """
    ingredient = args.재료명

    if ingredient == "목록":
        print("\n--- 🍣 마스터 셰프의 플레이북: 사용 가능한 재료 목록 ---")
        for i, item in enumerate(get_available_ingredients(), 1):
            print(f"{i}. {item}")
        print("---------------------------------------------------\n")
        print("💡 특정 재료의 자세한 레시피를 보려면: 'recipe <재료명>'")
        return

    recipe_info = RECIPES.get(ingredient)
    if not recipe_info:
        print(f"오류: '{ingredient}'에 대한 레시피는 아직 저의 플레이북에 없습니다.")
        print("사용 가능한 재료 목록을 보려면 'recipe 목록'을 입력하세요.")
        return

    print(f"\n--- 🍣 {ingredient} 준비 플레이북 ---")
    print(f"설명: {recipe_info['설명']}")
    print("✨ 준비 단계:")
    for i, step in enumerate(recipe_info["단계"], 1):
        print(f"  {i}. {step}")
    print("---------------------------------\n")

def handle_report(args):
    """
    'report' 명령을 처리합니다. 일일 준비 보고서를 생성합니다.
    하루 동안의 준비 작업을 요약하고, 이를 기반으로 마스터 셰프의 통찰력을 더한
    오늘의 추천 메뉴 아이디어를 제공합니다.

    Args:
        args (argparse.Namespace): argparse에 의해 파싱된 인자 객체.
    """
    if not PREPARATION_LOG:
        print("오늘 기록된 준비 내역이 없어 보고서를 생성할 수 없습니다.")
        return

    today_date = datetime.date.today().strftime("%Y년 %m월 %d일")
    print(f"\n--- 🍣 에도마에 스시 시코미 일일 보고서 ({today_date}) ---")

    prepared_summary = {}
    for entry in PREPARATION_LOG:
        ingredient = entry["재료"]
        step = entry["단계"]
        if ingredient not in prepared_summary:
            prepared_summary[ingredient] = []
        prepared_summary[ingredient].append(step)

    if not prepared_summary:
        print("\n오늘 준비된 재료가 없습니다. 더 많은 재료를 준비하여 보고서를 풍성하게 만들어 주세요.")
    else:
        print("\n[✔ 준비 완료 재료 요약]")
        for ingredient, steps in prepared_summary.items():
            # 중복 단계를 제거하고 정렬하여 보여줍니다.
            unique_steps = sorted(list(set(steps)))
            print(f"- {ingredient}: {len(unique_steps)}단계 완료 ({', '.join(unique_steps)})")

    print("\n[📊 총 준비 내역]")
    for entry in PREPARATION_LOG:
        print(f"[{entry['시간']}] {entry['재료']} - 단계: {entry['단계']}, 양: {entry['양']}")

    print("\n[💡 마스터 셰프의 추천 메뉴 아이디어]")
    # 준비된 재료를 기반으로 메뉴 아이디어를 제안합니다.
    prepared_ingredients_names = prepared_summary.keys()
    if "초밥밥" in prepared_ingredients_names and ("참치" in prepared_ingredients_names or "광어" in prepared_ingredients_names):
        print("- 신선한 참치와 광어로 구성된 오늘의 오마카세 스시를 자신 있게 선보이세요!")
    if "장어" in prepared_ingredients_names and "초밥밥" in prepared_ingredients_names:
        print("- 든든하고 기력 보충에 좋은 프리미엄 장어덮밥 (우나쥬)을 추천합니다!")
    if "계란말이" in prepared_ingredients_names:
        print("- 부드러운 계란말이 초밥 또는 따뜻한 계란말이 단품 요리를 준비해 보세요.")
    if "초생강" in prepared_ingredients_names:
        print("- 깔끔하게 입맛을 정리할 명품 초생강이 준비되어 있습니다.")
    if "간장" in prepared_ingredients_names:
        print("- 오랜 숙성을 거친 특제 간장이 준비되어, 어떤 스시와도 완벽한 조화를 이룰 것입니다.")
    if not prepared_ingredients_names:
         print("- 아직 준비된 재료가 적어 추천하기 어렵습니다. 더 많은 재료를 준비하여 훌륭한 메뉴를 구성해 보세요.")


    print("\n--- 수고하셨습니다. 오늘도 최고의 스시를 위해 노력해 주셔서 감사합니다. ---")
    print(f"--- 다음 준비 작업에 대한 지시를 기다리겠습니다. ---")
    print("------------------------------------------------------------------\n")


# --- 메인 파서 설정 (Main Parser Setup) ---

def main():
    """
    CLI 도구의 메인 실행 함수입니다.
    argparse를 사용하여 명령줄 인자를 파싱하고 해당 핸들러 함수를 호출합니다.
    """
    parser = argparse.ArgumentParser(
        prog="edomae_sushi_shikomi_playbook.py",
        description="""🍣 에도마에 스시 시코미 플레이북: 
        일본 레스토랑 마스터 셰프를 위한 일일 준비 작업 관리 도구입니다.
        주방의 모든 준비 과정을 기록하고, 현황을 파악하며, 오늘의 메뉴를 구상하는 데 도움을 줍니다.""",
        formatter_class=argparse.RawTextHelpHelpFormatter # 개행 문자 처리
    )

    subparsers = parser.add_subparsers(dest="command", help="사용 가능한 명령을 선택하세요.")

    # 'prepare' 명령
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="재료 준비 완료를 기록합니다.",
        description="""특정 재료의 준비 완료를 플레이북에 기록합니다. 
필요에 따라 준비된 '양'과 어떤 '단계'까지 완료되었는지 지정할 수 있습니다.
정확한 기록은 주방의 효율적인 운영을 돕습니다.""",
        formatter_class=argparse.RawTextHelpHelpFormatter
    )
    prepare_parser.add_argument(
        "재료명",
        type=str,
        help=f"준비할 재료의 이름입니다. (예: 초밥밥, 참치, 광어)\n사용 가능한 재료: {', '.join(get_available_ingredients())}"
    )
    prepare_parser.add_argument(
        "--양",
        type=str,
        help="준비된 재료의 양을 지정합니다. (예: 5kg, 100g, 10마리)"
    )
    prepare_parser.add_argument(
        "--단계",
        type=str,
        help="준비된 재료의 특정 단계를 지정합니다. (예: 해동, 초배합)\n특정 재료의 단계는 'recipe <재료명>' 명령으로 확인할 수 있습니다."
    )
    prepare_parser.set_defaults(func=handle_prepare)

    # 'status' 명령
    status_parser = subparsers.add_parser(
        "status",
        help="현재까지의 준비 현황을 확인합니다.",
        description="""현재 세션에서 기록된 모든 재료의 준비 현황을 출력합니다.
특정 재료에 대한 현황만 보고 싶다면 '--재료' 옵션을 사용하세요.
마스터 셰프는 항상 주방의 현황을 파악해야 합니다.""",
        formatter_class=argparse.RawTextHelpHelpFormatter
    )
    status_parser.add_argument(
        "--재료",
        type=str,
        help="특정 재료의 준비 현황만 필터링하여 봅니다. (예: 초밥밥)"
    )
    status_parser.set_defaults(func=handle_status)

    # 'recipe' 명령
    recipe_parser = subparsers.add_parser(
        "recipe",
        help="특정 재료의 레시피 단계를 조회합니다.",
        description="""지정된 재료의 상세한 준비 과정을 출력합니다.
마스터 셰프의 비법이 담긴 레시피를 확인하고, 완벽한 재료 준비를 위한 지침을 얻으세요.
사용 가능한 모든 재료 목록을 보려면 'recipe 목록'을 입력하세요.""",
        formatter_class=argparse.RawTextHelpHelpFormatter
    )
    recipe_parser.add_argument(
        "재료명",
        type=str,
        help=f"레시피를 조회할 재료의 이름입니다. '목록'을 입력하면 모든 사용 가능한 재료를 보여줍니다.\n사용 가능한 재료: {', '.join(get_available_ingredients())}, 목록"
    )
    recipe_parser.set_defaults(func=handle_recipe)

    # 'report' 명령
    report_parser = subparsers.add_parser(
        "report",
        help="일일 준비 보고서를 생성합니다.",
        description="""오늘 기록된 모든 준비 내역을 요약하고, 
이를 기반으로 마스터 셰프의 통찰력을 더한 간단한 추천 메뉴 아이디어를 제공합니다.
오늘의 스시를 구상하는 데 영감을 얻으세요.""",
        formatter_class=argparse.RawTextHelpHelpFormatter
    )
    report_parser.set_defaults(func=handle_report)

    args = parser.parse_args()

    # 파싱된 인자에 'func' 속성이 있다면 해당 함수를 호출
    if hasattr(args, 'func'):
        args.func(args)
    else:
        # 명령이 지정되지 않았을 경우 도움말 출력
        parser.print_help()

if __name__ == "__main__":
    main()