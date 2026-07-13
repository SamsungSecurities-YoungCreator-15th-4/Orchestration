# IPS 충돌·PB 승인 정책

기준일: 2026-07-13
정책 버전: `2026-07-13.v1`

## 결정 사항

`conflict_check`는 리스크 엔진 실행 전의 **적합성 red flag**만 검사한다. VaR·CVaR·스트레스
결과 한도 초과는 이 노드가 아니라 정량 엔진 이후의 Judge/리스크 정책에서 다룬다.

- `block`: 입력 또는 고객정보가 불완전해 계산의 전제가 성립하지 않는다. PB 예외 승인 불가.
- `review`: 공식 원칙을 팀 내부 사전검토 임계값으로 구체화한 경고다. PB가 충돌을 인지하고
  10자 이상의 사유를 남긴 `exception_approved`인 경우에만 **리스크 계산**을 허용한다.
- 승인은 `draft → reviewed → locked` 순서만 허용한다. `locked`는 거래·상품 권유 승인이
  아니라 리스크 계산 실행 승인이다(`trade_approval=false`).

## 공식 근거와 해석

1. [금융소비자 보호에 관한 법률 제17조](https://law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1020684777)는
   일반금융소비자의 상품 취득·처분 목적, 재산상황과 경험 등을 파악하고 부적합한 계약을
   권유하지 않도록 적합성 원칙을 정한다.
2. [금융투자협회 표준투자권유준칙](https://law.kofia.or.kr/service/law/lawFullScreenContent.do?historySeq=1614&seq=149)은
   투자자 성향과 상품 위험도의 적합성을 확인하고, 주식 및 유사 위험상품은 예시상 1년 이상의
   투자예정기간을 별도로 고려하는 것이 바람직하다고 안내한다. 부적합 상품을 투자자가 스스로
   청약하는 경우 별도 확인 절차도 제시한다.
3. [FINRA Rule 2111](https://www.finra.org/rules-guidance/rulebooks/finra-rules/2111)은
   투자자 프로필에 연령, 재산상황, 세금, 목적, 투자기간, 유동성 수요와 위험감수도를 포함하고
   고객별 적합성을 분석하도록 한다. 이 규칙은 국내 법적 기준으로 사용하지 않고 IPS 설계의
   보조 근거로만 사용한다.
4. [SEC Investor.gov 자산배분·분산 안내](https://www.investor.gov/introduction-investing/getting-started/asset-allocation)는
   투자기간과 위험감수도에 따라 자산배분이 달라지고, 자산군 분산이 손실 위험을 줄이는 기본
   원칙임을 설명한다. 이 역시 교육·방법론 근거이며 국내 법적 한도가 아니다.

## 내부 임계값

| 규칙 | 기준 | 등급 | 예외 승인 | 근거 성격 |
| --- | ---: | --- | --- | --- |
| 포트폴리오 평가금액 없음 | 총액 ≤ 0 | block | 불가 | 계산 전제 |
| 투자기간 미확인 | `Time ≤ 0` | block | 불가 | 적합성 필수정보 |
| 유동성 필요액이 총자산 초과 | 필요액 > 총자산 | block | 불가 | 충당 불가능 |
| 높은 유동성이나 금액 미확인 | `Liquidity=높음`, 금액 없음 | block | 불가 | 충당 검증 불가능 |
| 현금성자산 부족 | 필요액 > 현금성자산 | review | 가능 | 유동성 원칙 |
| 유동성 수요 과다 | 필요액 > 총자산 30% | review | 가능 | 팀 내부 보수 기준 |
| 단기 위험자산 | 1년 미만 + 주식·대체투자 > 0 | review | 가능 | 협회 투자기간 예시 |
| 균형형 위험자산 과다 | 주식·대체투자 > 60% | review | 가능 | 팀 내부 성향 매핑 |
| 단일 위험자산 집중 | 한 위험자산군 > 40% | review | 가능 | 분산 원칙의 내부 경보선 |

30%·60%·40%는 법령이나 협회의 법정 한도가 아니다. 현재 6개 자산군만 입력되는 과제용
포트폴리오에서 red flag를 재현 가능하게 검출하기 위한 팀 내부 임계값이며,
`config/ips_policy.yaml`에서 버전·출처와 함께 관리한다. 실제 상품 단위 위험등급과 PB 정책이
확정되면 별도 리뷰를 거쳐 조정해야 한다.

## 재현성과 감사

- 충돌 결과마다 `rule`, `severity`, `observed`, `limit`, `policy_version`, `policy_ref`,
  `evidence_refs`, `exception_allowed`를 남긴다.
- 실행 State에는 정책 파일 전체의 `policy_hash`를 저장한다.
- 승인 레코드에는 unresolved conflicts와 `approval_hash`를 남겨 이후 리포트에서 추적한다.
- gpt-4o 추출은 `temperature=0`, `seed=42`, 프롬프트·입력·출력 해시를 저장한다.
  Azure의 seed는 best-effort이므로 완전한 결정론을 보장하지 않는다. 실제 변동성은
  `scripts/evaluate_ips_extraction.py --repeats 3`으로 20개 회귀 사례에서 측정한다.
  합격 기준은 고객 적합성 판단에 직접 쓰이는 명시적 구조화 필드의 반복 일치율이며,
  자유서술 Tax·Legal·Unique까지 포함한 전체 출력 일치율도 별도 감사 지표로 함께 남긴다.
