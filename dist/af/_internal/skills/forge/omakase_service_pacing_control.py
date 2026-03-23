'오마카세 서비스 페이싱 조절' CLI 도구는 일본 레스토랑의 마스터 셰프가 오마카세 서비스의 흐름을 효율적으로 관리할 수 있도록 돕습니다. 전반적인 서비스 페이싱을 설정하거나, 특정 테이블에 대한 맞춤형 페이싱을 적용하고, 각 요리가 제공된 시간을 기록하며, 현재 서비스 진행 상황을 한눈에 파악할 수 있도록 설계되었습니다.


# omakase_service_pacing_control.py

import argparse
import datetime
import json
import os
import sys

# --- 전역 상수 및 설정 ---

# 페이싱 속도 옵션
PACING_OPTIONS = ["빠르게", "보통", "느리게"]

# 각 페이싱 속도에 따른 기본 분당 요리 시간 (분/요리)
DEFAULT_PACE_MIN_PER_DISH = {
    "빠르게": 3,
    "보통": 5,
    "느리게": 8,
}

# 서비스 상태를 저장할 파일 경로
# 실제 프로덕션 환경에서는 데이터베이스 등을 사용하는 것이 좋습니다.
STATE_FILE = "omakase_service_state.json"

# --- 서비스 상태 관리 ---

def get_default_state():
    """
    기본 서비스 상태 딕셔너리를 반환합니다.
    새로운 서비스 시작 시 또는 초기화 시 사용됩니다.
    """
    return {
        'global_pace_level': "보통",  # 전역 페이싱 레벨
        'global_min_per_dish': DEFAULT_PACE_MIN_PER_DISH["보통"],  # 전역 분당 요리 시간
        'total_dishes_planned': None,  # 전체 오마카세 코스의 총 요리 수
        'tables': {}  # 개별 테이블의 설정 및 서비스 기록
        # 'tables' 딕셔너리 예시:
        # {
        #     '1': {
        #         'pace_level': '느리게',
        #         'min_per_dish': 8,
        #         'dishes_served': [{'dish_name': '참치 뱃살', 'timestamp': '2023-10-27T10:30:00.123456'}],
        #         'last_served_time': '2023-10-27T10:30:00.123456'
        #     }
        # }
    }

def load_state():
    """
    서비스 상태를 파일에서 불러옵니다.
    파일이 없거나 유효하지 않으면 기본 상태를 반환합니다.
    datetime 객체는 JSON으로 직렬화되지 않으므로, 문자열에서 다시 변환하는 과정이 포함됩니다.
    """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                # datetime 문자열을 실제 datetime 객체로 변환
                if 'tables' in state:
                    for table_id, table_data in state['tables'].items():
                        if 'dishes_served' in table_data:
                            for served_dish in table_data['dishes_served']:
                                if 'timestamp' in served_dish and isinstance(served_dish['timestamp'], str):
                                    served_dish['timestamp'] = datetime.datetime.fromisoformat(served_dish['timestamp'])
                        if 'last_served_time' in table_data and isinstance(table_data['last_served_time'], str):
                            table_data['last_served_time'] = datetime.datetime.fromisoformat(table_data['last_served_time'])
                return state
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"경고: 상태 파일 로드 중 오류 발생 ({e}). 기본 상태로 초기화합니다.", file=sys.stderr)
            return get_default_state()
    return get_default_state()

def save_state(state):
    """
    현재 서비스 상태를 파일에 저장합니다.
    datetime 객체는 JSON으로 직렬화되지 않으므로, ISO 형식 문자열로 변환하여 저장합니다.
    """
    state_to_save = state.copy()
    if 'tables' in state_to_save:
        for table_id, table_data in state_to_save['tables'].items():
            if 'dishes_served' in table_data:
                for served_dish in table_data['dishes_served']:
                    if isinstance(served_dish.get('timestamp'), datetime.datetime):
                        served_dish['timestamp'] = served_dish['timestamp'].isoformat()
            if isinstance(table_data.get('last_served_time'), datetime.datetime):
                table_data['last_served_time'] = table_data['last_served_time'].isoformat()
    
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state_to_save, f, indent=4, ensure_ascii=False)

# 전역 서비스 상태 변수 초기화
SERVICE_STATE = load_state()

# --- 헬퍼 함수 ---

def _get_effective_pace_minutes(pace_level, min_per_dish, global_pace_level, global_min_per_dish):
    """
    주어진 테이블 설정 또는 전역 설정을 기반으로 실제 적용되는 분당 요리 시간을 반환합니다.
    테이블 설정이 우선하며, 없으면 전역 설정을 따르고, 그것마저 없으면 기본 '보통' 페이싱을 따릅니다.
    """
    if min_per_dish is not None:
        return min_per_dish
    if pace_level is not None:
        return DEFAULT_PACE_MIN_PER_DISH.get(pace_level, DEFAULT_PACE_MIN_PER_DISH["보통"])
    if global_min_per_dish is not None:
        return global_min_per_dish
    if global_pace_level is not None:
        return DEFAULT_PACE_MIN_PER_DISH.get(global_pace_level, DEFAULT_PACE_MIN_PER_DISH["보통"])
    return DEFAULT_PACE_MIN_PER_DISH["보통"] # 최후의 경우 기본값

# --- CLI 명령어 구현 ---

def handle_set_pacing(args):
    """
    전체 서비스 또는 특정 테이블의 페이싱 설정을 처리합니다.
    `--속도` 또는 `--분_당_요리` 중 하나를 사용하여 페이싱을 설정합니다.
    `--테이블` 인자를 사용하면 특정 테이블에 적용되고, 사용하지 않으면 전역 설정이 됩니다.
    `--총_요리_수`는 전역 오마카세 코스의 총 요리 개수를 설정합니다.
    """
    global SERVICE_STATE

    target_table_id = str(args.테이블) if args.테이블 is not None else None
    
    selected_pace_level = args.속도
    selected_min_per_dish = args.분_당_요리
    
    # 입력 유효성 검사
    if selected_pace_level and selected_min_per_dish:
        print("오류: '--속도'(-s)와 '--분_당_요리'(-m)는 동시에 설정할 수 없습니다. 둘 중 하나만 선택해주세요.", file=sys.stderr)
        return
    if not selected_pace_level and not selected_min_per_dish and args.총_요리_수 is None:
        print("오류: 설정할 페이싱 속도(--속도), 분당 요리 수(--분_당_요리) 또는 총 요리 수(--총_요리_수)를 지정해야 합니다.", file=sys.stderr)
        return

    # 설정 적용
    if target_table_id:
        # 테이블이 없으면 전역 설정을 기본으로 하여 새로 생성
        if target_table_id not in SERVICE_STATE['tables']:
            SERVICE_STATE['tables'][target_table_id] = {
                'pace_level': SERVICE_STATE['global_pace_level'],
                'min_per_dish': SERVICE_STATE['global_min_per_dish'],
                'dishes_served': [],
                'last_served_time': None
            }
        
        table_settings = SERVICE_STATE['tables'][target_table_id]
        
        if selected_pace_level:
            table_settings['pace_level'] = selected_pace_level
            table_settings['min_per_dish'] = DEFAULT_PACE_MIN_PER_DISH[selected_pace_level]
            print(f"테이블 {target_table_id}의 페이싱을 '{selected_pace_level}'({table_settings['min_per_per_dish']}분/요리)으로 설정했습니다.")
        elif selected_min_per_dish:
            table_settings['pace_level'] = None # 사용자 정의이므로 레벨은 없음
            table_settings['min_per_dish'] = selected_min_per_dish
            print(f"테이블 {target_table_id}의 페이싱을 '{selected_min_per_dish}'분/요리으로 설정했습니다.")
        else: # 속도나 분당 요리 수가 지정되지 않았다면
            print(f"테이블 {target_table_id}에 대해 페이싱 설정 변경은 없었습니다.")

        if args.총_요리_수 is not None:
            print(f"경고: 테이블 {target_table_id}에 대한 총 요리 수(--총_요리_수) 설정은 전역 설정에만 적용됩니다. 무시됩니다.", file=sys.stderr)

    else: # 전역 설정
        if selected_pace_level:
            SERVICE_STATE['global_pace_level'] = selected_pace_level
            SERVICE_STATE['global_min_per_dish'] = DEFAULT_PACE_MIN_PER_DISH[selected_pace_level]
            print(f"전체 서비스의 페이싱을 '{selected_pace_level}'({SERVICE_STATE['global_min_per_dish']}분/요리)으로 설정했습니다.")
        elif selected_min_per_dish:
            SERVICE_STATE['global_pace_level'] = None # 사용자 정의이므로 레벨은 없음
            SERVICE_STATE['global_min_per_dish'] = selected_min_per_dish
            print(f"전체 서비스의 페이싱을 '{selected_min_per_dish}'분/요리으로 설정했습니다.")

        if args.총_요리_수 is not None:
            SERVICE_STATE['total_dishes_planned'] = args.총_요리_수
            print(f"전체 오마카세의 총 요리 수를 {args.총_요리_수}개로 설정했습니다.")
        
    save_state(SERVICE_STATE)

def handle_get_status(args):
    """
    전체 서비스 또는 특정 테이블의 현재 페이싱 상태를 표시합니다.
    `--테이블` 인자를 사용하면 특정 테이블의 상세 현황을 확인합니다.
    """
    global SERVICE_STATE

    target_table_id = str(args.테이블) if args.테이블 is not None else None

    print("\n--- 오마카세 서비스 페이싱 상태 ---")

    if target_table_id:
        table_data = SERVICE_STATE['tables'].get(target_table_id)
        
        # 테이블 설정이 없으면 전역 설정을 따르는 것으로 간주
        effective_pace_level = table_data.get('pace_level', SERVICE_STATE['global_pace_level']) if table_data else SERVICE_STATE['global_pace_level']
        effective_min_per_dish = table_data.get('min_per_dish', SERVICE_STATE['global_min_per_dish']) if table_data else SERVICE_STATE['global_min_per_dish']
        
        current_min_per_dish = _get_effective_pace_minutes(
            effective_pace_level, 
            effective_min_per_dish, 
            SERVICE_STATE['global_pace_level'], 
            SERVICE_STATE['global_min_per_dish']
        )
        
        served_dishes = table_data.get('dishes_served', []) if table_data else []
        served_count = len(served_dishes)
        
        print(f"\n[테이블 {target_table_id}]")
        print(f"  현재 페이싱: {effective_pace_level if effective_pace_level else '사용자 지정'} ({current_min_per_dish}분/요리)")
        print(f"  제공된 요리 수: {served_count}개")

        if SERVICE_STATE['total_dishes_planned']:
            remaining_dishes = SERVICE_STATE['total_dishes_planned'] - served_count
            print(f"  남은 요리 수: {max(0, remaining_dishes)}개 (총 {SERVICE_STATE['total_dishes_planned']}개 중)")
            
            if remaining_dishes > 0:
                estimated_remaining_time_minutes = remaining_dishes * current_min_per_dish
                hours = int(estimated_remaining_time_minutes // 60)
                minutes = int(estimated_remaining_time_minutes % 60)
                time_str = ""
                if hours > 0:
                    time_str += f"{hours}시간 "
                if minutes > 0 or (hours == 0 and minutes == 0): # 0분도 표시
                    time_str += f"{minutes}분"
                print(f"  예상 남은 시간: 약 {time_str}")
            
        if served_count > 0 and served_dishes[-1]['timestamp']:
            last_served_time = served_dishes[-1]['timestamp']
            time_since_last_served = datetime.datetime.now() - last_served_time
            minutes_since_last_served = time_since_last_served.total_seconds() / 60
            
            print(f"  마지막 요리 제공 시각: {last_served_time.strftime('%H:%M:%S')}")
            print(f"  마지막 요리 후 경과 시간: {int(minutes_since_last_served)}분 {int(time_since_last_served.total_seconds() % 60)}초")

            if minutes_since_last_served > current_min_per_dish * 1.2: # 20% 이상 지연 시 경고
                print("  경고: 페이싱이 지연되고 있습니다! 고객 반응을 확인하세요.")
            elif served_count > 1 and minutes_since_last_served < current_min_per_dish * 0.8: # 첫 요리 후 20% 이상 빠를 시 경고
                 print("  경고: 페이싱이 너무 빠를 수 있습니다! 고객이 부담을 느낄 수 있습니다.")

    else: # 전체 서비스 상태
        global_min_per_dish = _get_effective_pace_minutes(
            SERVICE_STATE['global_pace_level'], 
            SERVICE_STATE['global_min_per_dish'], 
            None, None # 전역 설정을 확인할 때는 더 이상 상위 기본값이 없음
        )
        print(f"[전체 서비스 기본 설정]")
        print(f"  기본 페이싱: {SERVICE_STATE['global_pace_level'] if SERVICE_STATE['global_pace_level'] else '사용자 지정'} ({global_min_per_dish}분/요리)")
        print(f"  총 오마카세 요리 수: {SERVICE_STATE['total_dishes_planned'] if SERVICE_STATE['total_dishes_planned'] else '미설정'}")
        
        print(f"\n[테이블별 상세 현황]")
        if not SERVICE_STATE['tables']:
            print("  현재 서비스 중인 테이블이 없습니다.")
        else:
            sorted_table_ids = sorted(SERVICE_STATE['tables'].keys(), key=int)
            for table_id in sorted_table_ids:
                table_data = SERVICE_STATE['tables'][table_id]
                
                # 테이블별 유효 페이싱 계산 (테이블 설정이 우선)
                table_pace_level = table_data.get('pace_level')
                table_min_per_dish = table_data.get('min_per_dish')

                current_min_per_dish = _get_effective_pace_minutes(
                    table_pace_level, 
                    table_min_per_dish, 
                    SERVICE_STATE['global_pace_level'], 
                    SERVICE_STATE['global_min_per_dish']
                )

                served_count = len(table_data.get('dishes_served', []))
                
                status_str = f"  테이블 {table_id}: "
                status_str += f"페이싱: {table_pace_level if table_pace_level else '사용자 지정'} ({current_min_per_dish}분/요리), "
                status_str += f"제공된 요리: {served_count}개"

                if served_count > 0 and table_data['last_served_time']:
                    last_served_time = table_data['last_served_time']
                    time_since_last_served = datetime.datetime.now() - last_served_time
                    status_str += f", 마지막 제공: {last_served_time.strftime('%H:%M:%S')} ({int(time_since_last_served.total_seconds() // 60)}분 경과)"
                
                print(status_str)
    print("-----------------------------------")


def handle_serve_dish(args):
    """
    특정 테이블에 요리가 제공되었음을 기록합니다.
    `--테이블`은 필수 인자이며, `--요리_이름`으로 요리명을 지정할 수 있습니다.
    """
    global SERVICE_STATE

    table_id = str(args.테이블)
    dish_name = args.요리_이름 if args.요리_이름 else f"요리 #{len(SERVICE_STATE['tables'].get(table_id, {}).get('dishes_served', [])) + 1}"
    
    if table_id not in SERVICE_STATE['tables']:
        # 테이블이 새로 추가되면 전역 설정을 기본으로 하여 초기화
        SERVICE_STATE['tables'][table_id] = {
            'pace_level': SERVICE_STATE['global_pace_level'],
            'min_per_dish': SERVICE_STATE['global_min_per_dish'],
            'dishes_served': [],
            'last_served_time': None
        }

    table_data = SERVICE_STATE['tables'][table_id]
    
    current_time = datetime.datetime.now()
    
    served_dish_info = {
        'dish_name': dish_name,
        'timestamp': current_time
    }
    table_data['dishes_served'].append(served_dish_info)
    table_data['last_served_time'] = current_time

    print(f"테이블 {table_id}에 '{dish_name}'을(를) 제공했습니다. (현재 {len(table_data['dishes_served'])}번째 요리)")
    save_state(SERVICE_STATE)

def handle_reset_service(args):
    """
    모든 서비스 설정을 초기화합니다.
    사용자의 확인을 거쳐 서비스 상태 파일도 삭제합니다.
    """
    global SERVICE_STATE
    
    confirm = input("정말로 모든 서비스 상태를 초기화하시겠습니까? 이 작업은 되돌릴 수 없습니다. (y/N): ")
    if confirm.lower() == 'y':
        SERVICE_STATE = get_default_state()
        save_state(SERVICE_STATE) # 기본 상태로 저장
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE) # 상태 파일 삭제로 완전 초기화
        print("모든 서비스 상태가 초기화되었습니다.")
    else:
        print("서비스 초기화를 취소합니다.")

# --- Argparse 설정 ---

def main():
    """
    메인 함수: argparse를 설정하고 명령어를 파싱하여 해당 핸들러를 호출합니다.
    """
    parser = argparse.ArgumentParser(
        description="오마카세 서비스 페이싱 조절 도구\n\n"
                    "마스터 셰프가 오마카세 서비스의 흐름을 관리할 수 있도록 돕습니다.\n"
                    "전반적인 서비스 속도를 설정하고, 특정 테이블의 진행 상황을 추적하며,\n"
                    "각 요리 제공 시간을 기록하여 최적의 고객 경험을 제공합니다.",
        formatter_class=argparse.RawTextHelpFormatter # for multi-line help descriptions
    )

    subparsers = parser.add_subparsers(dest="action", help="수행할 작업")

    # '설정' (set) 명령어
    set_parser = subparsers.add_parser(
        "설정",
        help="전체 서비스 또는 특정 테이블의 페이싱을 설정합니다.",
        description="""
        전체 서비스 또는 특정 테이블의 오마카세 페이싱 속도를 설정합니다.
        '--속도'와 '--분_당_요리' 중 하나만 사용할 수 있습니다.
        '--테이블' 인자 없이 사용하면 전역 설정이 됩니다.
        '--총_요리_수'는 전역 설정에만 적용됩니다.
        """
    )
    set_parser_group = set_parser.add_mutually_exclusive_group()
    set_parser_group.add_argument(
        "-s", "--속도",
        choices=PACING_OPTIONS,
        help=f"페이싱 속도: {', '.join(PACING_OPTIONS)} 중 하나를 선택합니다."
    )
    set_parser_group.add_argument(
        "-m", "--분_당_요리",
        type=int,
        help="요리 하나당 예상되는 시간(분)을 직접 지정합니다. (예: 7)"
    )
    set_parser.add_argument(
        "-t", "--테이블",
        type=int,
        help="페이싱을 설정할 특정 테이블 번호 (예: 1, 2)"
    )
    set_parser.add_argument(
        "-d", "--총_요리_수",
        type=int,
        help="전체 오마카세 코스의 총 요리 개수를 설정합니다. (전역 설정에만 적용됨)"
    )
    set_parser.set_defaults(func=handle_set_pacing)

    # '확인' (status) 명령어
    get_parser = subparsers.add_parser(
        "확인",
        help="현재 서비스 또는 특정 테이블의 페이싱 상태를 확인합니다.",
        description="""
        현재 오마카세 서비스의 전반적인 페이싱 상태 또는
        특정 테이블의 상세 현황을 확인합니다.
        """
    )
    get_parser.add_argument(
        "-t", "--테이블",
        type=int,
        help="상태를 확인할 특정 테이블 번호 (예: 1, 2)"
    )
    get_parser.set_defaults(func=handle_get_status)

    # '제공' (serve) 명령어
    serve_parser = subparsers.add_parser(
        "제공",
        help="특정 테이블에 요리가 제공되었음을 기록합니다.",
        description="""
        테이블에 요리가 서빙되었을 때 이 명령어를 사용하여
        시간을 기록하고 페이싱 추적에 활용합니다.
        """
    )
    serve_parser.add_argument(
        "-t", "--테이블",
        type=int,
        required=True,
        help="요리를 제공한 테이블 번호 (필수)"
    )
    serve_parser.add_argument(
        "-n", "--요리_이름",
        type=str,
        help="제공된 요리의 이름 (예: 참치 뱃살, 고등어 봉초밥). 미지정 시 자동 생성."
    )
    serve_parser.set_defaults(func=handle_serve_dish)

    # '초기화' (reset) 명령어
    reset_parser = subparsers.add_parser(
        "초기화",
        help="모든 서비스 상태를 기본값으로 초기화합니다.",
        description="""
        서비스의 모든 페이싱 설정, 제공된 요리 기록 등을
        초기 상태로 되돌립니다. 신중하게 사용하십시오.
        """
    )
    reset_parser.set_defaults(func=handle_reset_service)

    args = parser.parse_args()

    # 서브커맨드가 지정되지 않았을 경우 도움말 출력
    if not hasattr(args, 'func'):
        parser.print_help()
    else:
        args.func(args)

if __name__ == "__main__":
    main()