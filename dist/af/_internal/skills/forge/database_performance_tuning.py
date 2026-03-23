`database_performance_tuning.py`


import argparse
import sys

def analyze_slow_query(args):
    """
    느린 쿼리를 분석하고 최적화 방안을 제안합니다.

    Args:
        args: argparse 네임스페이스 객체. query, duration 속성을 포함합니다.
    """
    print("\n--- 느린 쿼리 분석 결과 ---")
    if args.query:
        print(f"분석 대상 쿼리: '{args.query}'")
    if args.duration is not None:
        print(f"실행 시간: {args.duration} 초")

    print("\n[일반적인 최적화 권고 사항]")
    if args.duration is not None and args.duration > 1.0:
        print("  - 쿼리 실행 시간이 1초를 초과하여 느리다고 판단됩니다.")
        print("  - 쿼리 실행 계획(EXPLAIN)을 분석하여 병목 지점을 찾으십시오.")
        print("  - WHERE 절, JOIN 조건, ORDER BY 절에 사용되는 컬럼에 적절한 인덱스가 있는지 확인하십시오.")
        print("  - 불필요한 데이터를 조회하거나 과도한 JOIN이 발생하는지 검토하십시오.")
        print("  - 서브쿼리 대신 JOIN을 사용하거나, 가능하다면 CTE(Common Table Expression)를 활용하십시오.")
        print("  - 대량의 데이터를 처리하는 경우 배치 처리 또는 페이지네이션을 고려하십시오.")
    else:
        print("  - 쿼리 실행 시간이 양호하거나 추가 정보가 필요합니다.")
        print("  - 주기적으로 느린 쿼리 로그를 검토하여 잠재적인 성능 저하를 방지하십시오.")
        print("  - 쿼리의 복잡도에 비해 실행 시간이 예상보다 길다면, 실행 계획 분석이 필요합니다.")
    print("----------------------------")

def analyze_missing_indexes(args):
    """
    누락된 인덱스를 제안합니다.

    Args:
        args: argparse 네임스페이스 객체. table, columns 속성을 포함합니다.
    """
    print("\n--- 누락된 인덱스 분석 결과 ---")
    if args.table:
        print(f"대상 테이블: '{args.table}'")
    if args.columns:
        print(f"쿼리에서 자주 사용되는 컬럼: {', '.join(args.columns)}")

    print("\n[인덱스 제안]")
    if not args.table or not args.columns:
        print("  - 테이블명과 컬럼 정보를 제공해야 정확한 인덱스 제안이 가능합니다.")
        print("  - 느린 쿼리 로그나 데이터베이스 통계(예: PostgreSQL의 pg_stat_statements)를 기반으로 인덱스 생성 후보를 식별하십시오.")
    else:
        if len(args.columns) > 1:
            print(f"  - 테이블 '{args.table}'의 컬럼 ({', '.join(args.columns)})에 복합 인덱스 생성을 고려하십시오.")
            print(f"    예시: CREATE INDEX idx_{args.table}_{'_'.join(args.columns)} ON {args.table} ({', '.join(args.columns)});")
        else:
            print(f"  - 테이블 '{args.table}'의 컬럼 '{args.columns[0]}'에 단일 컬럼 인덱스 생성을 고려하십시오.")
            print(f"    예시: CREATE INDEX idx_{args.table}_{args.columns[0]} ON {args.table} ({args.columns[0]});")
        print("  - WHERE 절 조건과 ORDER BY 절에 사용되는 컬럼 순서를 고려하여 인덱스를 생성하십시오.")
        print("  - 인덱스가 과도하게 많으면 쓰기 성능(INSERT, UPDATE, DELETE)에 영향을 줄 수 있으므로 주의하십시오.")
        print("  - 기존 인덱스와 중복되거나 비효율적인 인덱스는 제거하는 것을 고려하십시오.")
    print("------------------------------")

def analyze_maintenance(args):
    """
    데이터베이스 유지보수 작업을 권고합니다.

    Args:
        args: argparse 네임스페이스 객체. db_type 속성을 포함합니다.
    """
    print("\n--- 데이터베이스 유지보수 권고 ---")
    print(f"대상 데이터베이스 유형: '{args.db_type}'")

    print("\n[유지보수 작업 권고 사항]")
    if args.db_type.lower() == 'postgresql':
        print("  - PostgreSQL은 주기적인 VACUUM 작업이 필수적입니다. 특히 VACUUM FULL은 데이터 파일을 재구성하지만, 장시간 잠금을 유발하므로 신중하게 사용하십시오.")
        print("  - ANALYZE 명령을 사용하여 쿼리 플래너가 최적의 실행 계획을 세울 수 있도록 테이블 통계를 최신으로 유지하십시오.")
        print("  - 인덱스가 심하게 단편화된 경우 REINDEX 작업을 고려할 수 있습니다. (장시간 잠금 유의)")
        print("  - pg_repack과 같은 확장 프로그램을 사용하여 온라인으로 테이블 및 인덱스 재구성을 검토하십시오.")
    elif args.db_type.lower() == 'mysql':
        print("  - InnoDB 엔진의 경우 OPTIMIZE TABLE 명령은 테이블과 인덱스의 단편화를 제거하고 공간을 확보할 수 있습니다. (MyISAM에 더 효과적)")
        print("  - ANALYZE TABLE 명령을 사용하여 테이블 통계를 업데이트하십시오.")
        print("  - InnoDB는 자동적으로 많은 유지보수 작업을 수행하지만, 때때로 수동 분석이 필요할 수 있습니다.")
        print("  - 데이터베이스 크기가 크거나 많은 삭제 작업이 있었다면, 주기적인 OPTIMIZE TABLE을 고려하십시오.")
    elif args.db_type.lower() == 'oracle':
        print("  - 주기적인 통계 수집(DBMS_STATS 패키지)을 통해 옵티마이저가 최적의 실행 계획을 생성하도록 해야 합니다.")
        print("  - 인덱스 리빌드(ALTER INDEX REBUILD)는 일반적으로 필요한 경우가 드물며, 성능 개선 효과가 미미할 수 있으니 신중하게 결정하십시오.")
        print("  - 테이블 스페이스 사용률을 모니터링하고 필요한 경우 확장하십시오.")
    else:
        print(f"  - '{args.db_type}'에 대한 구체적인 유지보수 정보는 제공하기 어렵습니다.")
        print("  - 해당 데이터베이스의 공식 문서를 참조하여 적절한 유지보수 전략을 수립하십시오.")
    print("  - 모든 유지보수 작업 전에 반드시 백업을 수행하고, 테스트 환경에서 먼저 검증하십시오.")
    print("-----------------------------")

def analyze_connection_pool(args):
    """
    데이터베이스 커넥션 풀 설정을 분석하고 권고합니다.

    Args:
        args: argparse 네임스페이스 객체. 현재는 추가 인자 없음.
    """
    print("\n--- 데이터베이스 커넥션 풀 분석 ---")
    print("\n[커넥션 풀 설정 권고 사항]")
    print("  - 애플리케이션의 동시 요청 수와 데이터베이스 서버의 부하를 고려하여 커넥션 풀의 최대 크기(max_connections, max_pool_size)를 설정하십시오.")
    print("  - 너무 적은 풀 크기는 쿼리 대기 시간을 증가시키고, 너무 많은 풀 크기는 데이터베이스 서버에 과부하를 줄 수 있습니다.")
    print("  - 유휴 커넥션의 유지 시간(idle_timeout)을 설정하여 불필요하게 커넥션이 오래 유지되는 것을 방지하십시오.")
    print("  - 커넥션 획득 대기 시간(connection_timeout)을 설정하여 무한 대기를 방지하고 실패를 빠르게 감지하십시오.")
    print("  - 커넥션 유효성 검사(validation_query)를 주기적으로 수행하여 끊어진 커넥션을 제거하십시오.")
    print("  - HikariCP(Java), SQLAlchemy Pool(Python) 등 각 언어/프레임워크에 맞는 고성능 커넥션 풀 라이브러리를 사용하십시오.")
    print("  - 데이터베이스 서버의 max_connections 설정과 애플리케이션의 총 커넥션 풀 크기를 조화롭게 관리하십시오.")
    print("-----------------------------------")

def analyze_cache(args):
    """
    데이터베이스 캐싱 전략을 분석하고 권고합니다.

    Args:
        args: argparse 네임스페이스 객체. 현재는 추가 인자 없음.
    """
    print("\n--- 데이터베이스 캐싱 전략 분석 ---")
    print("\n[캐싱 전략 권고 사항]")
    print("  - 자주 읽히지만 자주 변경되지 않는 데이터를 대상으로 애플리케이션 레벨 캐싱(예: Redis, Memcached)을 도입하십시오.")
    print("  - ORM(Object-Relational Mapping)의 2차 캐시 기능을 활용하여 반복적인 쿼리를 줄이십시오.")
    print("  - 데이터베이스 자체 캐시(예: MySQL Query Cache는 일반적으로 사용되지 않음, PostgreSQL Shared Buffers)는 운영체제 캐시 및 애플리케이션 캐시와 함께 고려하십시오.")
    print("  - 캐시 무효화(Cache Invalidation) 전략을 신중하게 설계하여 오래된 데이터가 제공되지 않도록 하십시오.")
    print("  - 분산 캐시 시스템을 사용하여 여러 애플리케이션 서버 간에 캐시를 공유하고 확장성을 확보하십시오.")
    print("  - 캐시 히트율, 미스율 등 캐시 통계를 모니터링하여 캐싱 전략의 효율성을 평가하십시오.")
    print("  - 캐시 도입 전에 병목 지점이 캐시로 해결 가능한지 명확히 파악하십시오.")
    print("-----------------------------------")


def parse_arguments():
    """
    명령줄 인자를 파싱하고 `argparse.ArgumentParser` 객체를 반환합니다.

    Returns:
        argparse.ArgumentParser: 파싱된 인자를 담고 있는 ArgumentParser 객체.
    """
    parser = argparse.ArgumentParser(
        description="데이터베이스 성능 튜닝을 위한 전문 CLI 도구입니다.",
        epilog="백엔드 개발자를 위한 데이터베이스 최적화 가이드입니다."
    )

    subparsers = parser.add_subparsers(dest="command", help="실행할 명령을 선택하십시오.")

    # 'slow-query' 서브커맨드
    slow_query_parser = subparsers.add_parser(
        "slow-query",
        help="느린 쿼리를 분석하고 최적화 방안을 제안합니다.",
        description="특정 쿼리의 실행 시간 데이터를 기반으로 최적화 방안을 제시합니다."
    )
    slow_query_parser.add_argument(
        "--query",
        type=str,
        help="분석할 느린 SQL 쿼리 문자열. (선택 사항)",
        metavar="SQL_QUERY"
    )
    slow_query_parser.add_argument(
        "--duration",
        type=float,
        help="쿼리 실행 시간(초).",
        metavar="SECONDS"
    )
    slow_query_parser.set_defaults(func=analyze_slow_query)

    # 'missing-indexes' 서브커맨드
    missing_indexes_parser = subparsers.add_parser(
        "missing-indexes",
        help="누락된 인덱스를 제안합니다.",
        description="특정 테이블과 컬럼 사용 패턴에 기반하여 인덱스 생성을 권고합니다."
    )
    missing_indexes_parser.add_argument(
        "--table",
        type=str,
        required=True,
        help="인덱스 제안 대상 테이블명.",
        metavar="TABLE_NAME"
    )
    missing_indexes_parser.add_argument(
        "--columns",
        type=str,
        nargs='+',
        required=True,
        help="WHERE/ORDER BY 절 등에서 자주 사용되는 컬럼명. 공백으로 구분합니다.",
        metavar="COLUMN_NAME"
    )
    missing_indexes_parser.set_defaults(func=analyze_missing_indexes)

    # 'maintenance' 서브커맨드
    maintenance_parser = subparsers.add_parser(
        "maintenance",
        help="데이터베이스 유지보수 작업을 권고합니다.",
        description="특정 데이터베이스 유형에 따른 유지보수 작업을 제안합니다."
    )
    maintenance_parser.add_argument(
        "--db-type",
        type=str,
        choices=['postgresql', 'mysql', 'oracle'],
        required=True,
        help="대상 데이터베이스 유형 (postgresql, mysql, oracle 중 하나).",
        metavar="DB_TYPE"
    )
    maintenance_parser.set_defaults(func=analyze_maintenance)
    
    # 'connection-pool' 서브커맨드
    connection_pool_parser = subparsers.add_parser(
        "connection-pool",
        help="데이터베이스 커넥션 풀 설정을 권고합니다.",
        description="효율적인 커넥션 풀 관리를 위한 일반적인 가이드라인을 제공합니다."
    )
    connection_pool_parser.set_defaults(func=analyze_connection_pool)

    # 'cache' 서브커맨드
    cache_parser = subparsers.add_parser(
        "cache",
        help="데이터베이스 캐싱 전략을 권고합니다.",
        description="애플리케이션 및 데이터베이스 레벨에서의 캐싱 전략에 대한 조언을 제공합니다."
    )
    cache_parser.set_defaults(func=analyze_cache)

    return parser

def main():
    """
    CLI 도구의 메인 실행 함수입니다.
    인자를 파싱하고 해당 함수를 호출하여 데이터베이스 성능 튜닝 작업을 수행합니다.
    """
    parser = parse_arguments()
    args = parser.parse_args()

    if not hasattr(args, 'func'):
        # 서브커맨드 없이 메인 명령만 실행했을 경우
        parser.print_help()
        sys.exit(1)
    
    try:
        args.func(args)
    except Exception as e:
        print(f"\n오류 발생: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()


### 사용 방법 (How to use)

1.  **파일 저장:** 위 코드를 `database_performance_tuning.py` 파일로 저장합니다.
2.  **도움말 확인:**
    bash
    python database_performance_tuning.py --help
    python database_performance_tuning.py slow-query --help
    python database_performance_tuning.py missing-indexes --help
    # 등 각 서브커맨드별 도움말 확인 가능
    

3.  **예시 실행:**

    *   **느린 쿼리 분석:**
        bash
        python database_performance_tuning.py slow-query --query "SELECT * FROM users WHERE status='active' AND created_at < '2023-01-01' ORDER BY id DESC" --duration 5.2
        
        bash
        python database_performance_tuning.py slow-query --query "SELECT name FROM products WHERE category_id=10" --duration 0.1
        

    *   **누락된 인덱스 제안:**
        bash
        python database_performance_tuning.py missing-indexes --table orders --columns customer_id order_date status
        
        bash
        python database_performance_tuning.py missing-indexes --table users --columns email
        

    *   **데이터베이스 유지보수 권고 (PostgreSQL):**
        bash
        python database_performance_tuning.py maintenance --db-type postgresql
        

    *   **데이터베이스 유지보수 권고 (MySQL):**
        bash
        python database_performance_tuning.py maintenance --db-type mysql
        

    *   **데이터베이스 유지보수 권고 (Oracle):**
        bash
        python database_performance_tuning.py maintenance --db-type oracle
        

    *   **커넥션 풀 설정 권고:**
        bash
        python database_performance_tuning.py connection-pool
        

    *   **캐싱 전략 권고:**
        bash
        python database_performance_tuning.py cache
        

### 코드 설명

*   **`argparse` 활용:** Python 표준 라이브러리인 `argparse`를 사용하여 강력하고 유연한 명령줄 인터페이스를 구현했습니다.
*   **서브커맨드 구조:** `slow-query`, `missing-indexes`, `maintenance`, `connection-pool`, `cache`와 같은 서브커맨드를 통해 여러 가지 데이터베이스 튜닝 시나리오를 효과적으로 분리하고 관리할 수 있습니다.
*   **Docstring (한국어):** 모든 함수와 파일 상단에 한국어로 된 Docstring을 작성하여 코드의 목적, 인자, 반환 값 등을 명확히 설명합니다. 이는 백엔드 개발자가 코드를 이해하고 유지보수하는 데 큰 도움이 됩니다.
*   **한국어 출력 메시지:** 사용자에게 보여지는 모든 출력 메시지를 한국어로 작성하여 국내 사용자들이 직관적으로 도구를 사용할 수 있도록 했습니다.
*   **모듈화:** 각 튜닝 시나리오를 별도의 함수(`analyze_slow_query`, `analyze_missing_indexes` 등)로 분리하여 코드의 가독성과 유지보수성을 높였습니다.
*   **오류 처리:** `main` 함수에서 예외 처리를 추가하여 예상치 못한 오류 발생 시 사용자에게 친화적인 메시지를 제공하고 프로그램을 안전하게 종료하도록 했습니다.
*   **`sys.exit(1)`:** 오류 발생 시 `sys.exit(1)`을 호출하여 셸에 실패 상태를 알립니다.
*   **타입 힌트 및 `metavar`:** `argparse` 인자에 `type`과 `metavar`를 적절히 사용하여 도움말 메시지를 더욱 명확하게 만들었습니다. `choices`를 사용하여 특정 인자의 유효한 값들을 제한합니다.
*   **실질적인 조언:** 각 서브커맨드의 출력은 실제 백엔드 개발자들이 데이터베이스 성능 튜닝 시 고려해야 할 구체적인 조언들을 담고 있습니다. (예: `EXPLAIN` 분석, 복합 인덱스, `VACUUM`, `OPTIMIZE TABLE`, 캐싱 전략 등)