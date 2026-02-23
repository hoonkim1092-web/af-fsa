#!/usr/bin/env python3
"""
precision_knife_techniques.py

일식당 마스터 셰프(Japanese Restaurant Master Chef)의 정교한 칼질 기술을 시뮬레이션하는 CLI 도구입니다.
다양한 칼질 기법을 사용하여 식재료를 다듬고, 셰프의 생명인 칼을 연마하며, 
준비된 재료로 오마카세(Omakase)를 구성할 수 있습니다.
"""

import argparse
import sys
import time
from typing import List


class MasterChef:
    """일식 마스터 셰프의 도구와 기술을 관리하는 클래스입니다."""

    # 칼질 기법에 대한 한국어 설명 매핑
    TECHNIQUES = {
        "hiragiri": "평썰기 (히라기리 - 가장 기본적인 직각 썰기)",
        "sogigiri": "포뜨기 (소기기리 - 칼을 눕혀 얇게 저며 썰기)",
        "katsuramuki": "돌려깎기 (가쓰라무키 - 무 등을 끊기지 않고 얇게 깎아내기)",
        "itozukuri": "채썰기 (이토즈쿠리 - 실처럼 아주 가늘게 썰기)"
    }

    # 칼 종류에 대한 한국어 설명 매핑
    KNIVES = {
        "yanagiba": "야나기바 (사시미 전용 칼)",
        "deba": "데바 (생선 뼈를 자르고 다듬는 두꺼운 칼)",
        "usuba": "우수바 (채소 전용 얇은 칼)"
    }

    @classmethod
    def slice_ingredient(cls, ingredient: str, technique: str, thickness: float) -> None:
        """
        주어진 식재료를 마스터 셰프의 기술로 썰어냅니다.

        Args:
            ingredient (str): 썰어낼 식재료의 이름.
            technique (str): 사용할 일식 칼질 기법.
            thickness (float): 썰어낼 두께 (mm 단위).
        """
        tech_name = cls.TECHNIQUES.get(technique, technique)
        
        print("\n[ 셰프가 숨을 고르고 칼을 쥡니다... ]")
        time.sleep(1)
        
        if thickness < 1.0:
            print(f"🔪 고도의 집중력으로 '{ingredient}'을(를) {thickness}mm의 투명할 정도로 얇은 두께로 썰어냅니다.")
        elif thickness > 10.0:
            print(f"🔪 '{ingredient}'을(를) 식감을 살리기 위해 {thickness}mm 두께로 두툼하게 썰어냅니다.")
        else:
            print(f"🔪 '{ingredient}'을(를) {thickness}mm 두께로 정교하게 썰어냅니다.")
            
        print(f"✔️  사용된 기술: {tech_name}")
        print("✨ 완벽한 단면입니다. 식재료의 세포가 파괴되지 않아 본연의 맛이 극대화되었습니다.\n")

    @classmethod
    def sharpen_knife(cls, knife_type: str, grit: int) -> None:
        """
        최고의 절삭력을 유지하기 위해 숫돌에 칼을 연마합니다.

        Args:
            knife_type (str): 연마할 칼의 종류.
            grit (int): 사용할 숫돌의 방수.
        """
        knife_name = cls.KNIVES.get(knife_type, knife_type)
        
        print(f"\n[ {knife_name} 연마를 시작합니다. 숫돌 방수: #{grit} ]")
        
        if grit < 1000:
            stage = "초벌 갈기 (이빠진 곳과 형태를 잡습니다)"
        elif grit <= 3000:
            stage = "중벌 갈기 (날을 세우고 절삭력을 높입니다)"
        else:
            stage = "마무리 갈기 (경면 처리를 통해 최상의 예리함을 만듭니다)"
            
        print(f"💧 숫돌에 물을 적십니다... 단계: {stage}")
        
        # 연마 애니메이션 효과
        for i in range(3):
            time.sleep(0.8)
            print("   슥... 삭...")
            
        print("✨ 칼날이 거울처럼 빛납니다. 종이도 무게만으로 썰릴 만큼 예리해졌습니다.\n")

    @classmethod
    def prepare_omakase(cls, ingredients: List[str]) -> None:
        """
        준비된 식재료들을 바탕으로 오늘의 오마카세 메뉴를 선언합니다.

        Args:
            ingredients (List[str]): 오마카세에 사용될 식재료 목록.
        """
        print("\n🍣 [ 오늘의 오마카세 (お任せ) 준비 완료 ] 🍣")
        print("-" * 40)
        print("마스터 셰프가 다음의 엄선된 재료들로 코스를 시작합니다:")
        for idx, item in enumerate(ingredients, 1):
            print(f"  {idx}. {item}")
        print("-" * 40)
        print("손님, 정성을 다해 모시겠습니다. (이랏샤이마세!)\n")


def main() -> None:
    """CLI 도구의 진입점(Entry point)입니다. 인자를 파싱하고 적절한 메서드를 호출합니다."""
    parser = argparse.ArgumentParser(
        description="🔪 일식 마스터 셰프의 정교한 칼질 기술 및 주방 관리 CLI 도구",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest="command", help="실행할 작업을 선택하세요.")

    # 1. 식재료 썰기 (slice) 서브파서
    slice_parser = subparsers.add_parser("slice", help="정교한 기술로 식재료 썰기")
    slice_parser.add_argument(
        "-i", "--ingredient", 
        required=True, 
        help="다듬을 식재료 (예: 참치, 광어, 무)"
    )
    slice_parser.add_argument(
        "-t", "--technique", 
        choices=["hiragiri", "sogigiri", "katsuramuki", "itozukuri"], 
        required=True, 
        help="칼질 기법:\n"
             "  hiragiri   : 평썰기\n"
             "  sogigiri   : 포뜨기\n"
             "  katsuramuki: 돌려깎기\n"
             "  itozukuri  : 채썰기"
    )
    slice_parser.add_argument(
        "-th", "--thickness", 
        type=float, 
        required=True, 
        help="목표 두께 (mm 단위, 예: 1.5)"
    )

    # 2. 칼 갈기 (sharpen) 서브파서
    sharpen_parser = subparsers.add_parser("sharpen", help="셰프의 칼을 숫돌에 연마하기")
    sharpen_parser.add_argument(
        "-k", "--knife", 
        choices=["yanagiba", "deba", "usuba"], 
        required=True, 
        help="연마할 칼 종류:\n"
             "  yanagiba: 사시미 칼\n"
             "  deba    : 뼈 자르는 두꺼운 칼\n"
             "  usuba   : 채소용 칼"
    )
    sharpen_parser.add_argument(
        "-g", "--grit", 
        type=int, 
        default=1000, 
        help="숫돌의 방수 (기본값: 1000, 높을수록 정밀한 마감)"
    )

    # 3. 오마카세 메뉴 준비 (omakase) 서브파서
    omakase_parser = subparsers.add_parser("omakase", help="오늘의 오마카세 코스 구성하기")
    omakase_parser.add_argument(
        "ingredients", 
        nargs="+", 
        help="코스에 포함할 식재료 목록 (공백으로 구분)"
    )

    args = parser.parse_args()

    # 명령어가 입력되지 않은 경우 도움말 출력 후 종료
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 마스터 셰프 인스턴스화 없이 클래스 메서드로 바로 실행
    try:
        if args.command == "slice":
            MasterChef.slice_ingredient(args.ingredient, args.technique, args.thickness)
        elif args.command == "sharpen":
            MasterChef.sharpen_knife(args.knife, args.grit)
        elif args.command == "omakase":
            MasterChef.prepare_omakase(args.ingredients)
    except Exception as e:
        print(f"\n❌ [오류 발생]: 작업 중 문제가 발생했습니다. ({str(e)})")
        sys.exit(1)


if __name__ == "__main__":
    main()