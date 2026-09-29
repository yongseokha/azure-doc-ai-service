import asyncio

from app.core.config import settings
from app.utils.rate_limiter import SlidingWindowRateLimiter

# 약관 검증 계열 서비스(기존 약관 검증 / 검증 원문 기반 약관 검증)가 모두 이 모듈의 객체를 공유한다.
# 서비스마다 따로 두면 두 서비스의 job이 동시에 돌면서 Azure OpenAI 호출이 배로 늘어난다.

# job이 여러 개 동시에 들어와도 Azure OpenAI 호출은 한 번에 한 job씩만 나가도록 직렬화한다.
# (job마다 세마포어를 따로 두면 job 수만큼 동시 호출이 배로 늘어나 rate limit에 취약해진다.)
job_lock = asyncio.Lock()

# job(rqtKey) 하나가 짧은 시간에 만들어내는 LLM 호출이 분당 60회를 넘지 않도록 조절한다.
# (거부가 아니라 대기: 한도를 넘으면 자리가 날 때까지 기다렸다가 계속 진행한다.)
llm_rate_limiter = SlidingWindowRateLimiter(
    max_calls=settings.terms_verification_llm_max_calls_per_minute, period_seconds=60.0
)
