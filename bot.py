import discord
from discord.ext import commands, tasks
import aiohttp
import json
import re
import os
import html
import datetime
from datetime import time, timezone, timedelta
import random
import traceback

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# 채널 ID 설정 (int 형으로 고정)
NEWS_CHANNEL_ID = 1480944831600656384  
VOTE_CHANNEL_ID = 1484797598241128598  

# 타임존 및 스케줄 시간 설정 (한국 시간 오후 1시)
KST = timezone(timedelta(hours=9))
SCHEDULED_VOTE_TIME = time(hour=13, minute=0, second=0, tzinfo=KST)

# 티어 정보 및 딕셔너리
TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())

# 인증 대기 유저 저장용 딕셔너리
pending_users = {}
# ===============================================

# [로그 설정]
def log(message):
    print(f"[{datetime.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

# 봇 인스턴스 설정
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

class LoLBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.session = None  # 세션 공유를 위한 변수

    async def setup_hook(self):
        # 봇 시작 시 전역 HTTP 세션 생성
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        self.session = aiohttp.ClientSession(headers=headers)
        log("공용 HTTP 세션 생성 완료")
        
    async def close(self):
        # 봇 종료 시 세션 안전하게 닫기
        if self.session:
            await self.session.close()
        await super().close()

bot = LoLBot()

# ================= [ 핵심 기능: 뉴스 크롤링 ] =================
async def fetch_and_post_news():
    log("롤 공식 홈페이지 뉴스 체크 시작...")
    news_url = "https://www.leagueoflegends.com/ko-kr/news/" 
    
    try:
        channel = await bot.fetch_channel(NEWS_CHANNEL_ID)
        
        # bot.session을 재사용하여 통신 최적화
        async with bot.session.get(news_url) as response:
            if response.status != 200:
                log(f"홈페이지 접근 실패 (상태 코드: {response.status})")
                return

            raw_html = await response.text()
            
            # JSON 데이터 정규식 추출
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw_html)
            if not match:
                log("데이터를 찾을 수 없습니다. (페이지 구조 변경 의심)")
                return
            
            next_data = json.loads(match.group(1))
            blades = next_data.get('props', {}).get('pageProps', {}).get('page', {}).get('blades',[])
            
            articles_data =[]
            for blade in blades:
                if blade.get('type') == 'articleCardGrid' and 'items' in blade:
                    articles_data = blade['items']
                    break
                    
            log(f"홈페이지에서 찾은 뉴스 개수: {len(articles_data)}개")
            
            if not articles_data:
                return

            # 상위 10개 추출 및 역순(과거->최신) 정렬
            target_articles = articles_data[:10]
            target_articles.reverse()
            
            # 이미 포스팅된 링크 수집 (중복 방지)
            already_posted_links =[]
            async for msg in channel.history(limit=100):
                if msg.author == bot.user and msg.embeds:
                    already_posted_links.append(msg.embeds[0].url)
                    
            # 뉴스 데이터 파싱 및 전송
            for article in target_articles:
                # 1. 링크 추출 및 보정
                link_url = article.get('action', {}).get('payload', {}).get('url', '')
                if not link_url: 
                    continue
                if not link_url.startswith('http'):
                    link_url = "https://www.leagueoflegends.com" + link_url
                    
                if link_url in already_posted_links: 
                    continue
                    
                # 2. 제목 및 설명 추출
                title = article.get('title', '새로운 소식')
                raw_desc = article.get('description', {}).get('body', '클릭하여 자세한 내용을 확인하세요.')
                description = re.sub(r'<[^>]+>', '', raw_desc).strip()
                
                # 3. 이미지 추출
                image_url = article.get('media', {}).get('url', '')
                if image_url:
                    image_url = html.unescape(image_url).strip()
                    
                # 4. 작성일 포맷 변환
                published_at = article.get('publishedAt', '')
                date_text = "새 소식"
                
                if published_at:
                    try:
                        # ISO 포맷 변환 및 KST 적용
                        dt = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                        dt_korea = dt.astimezone(KST)
                        date_text = dt_korea.strftime("%Y년 %m월 %d일 %H:%M")
                    except Exception as date_error:
                        log(f"날짜 변환 에러: {date_error}")
                    
                # 5. 디스코드 임베드 생성 및 전송
                embed = discord.Embed(
                    title=title,
                    url=link_url,
                    description=description,
                    color=ffffff
                )
                
                if image_url and image_url.startswith('http'):
                    embed.set_image(url=image_url)
                
                embed.set_footer(text=date_text)

                try:
                    await channel.send(embed=embed)
                    log(f"포스팅 완료: {title}")
                except Exception as send_error:
                    log(f"전송 실패: {title} - {send_error}")

    except Exception as e:
        log(f"크롤링 전체 에러 발생: {e}")
        traceback.print_exc()

# ================= [ 자동 루프 구역 ] =================
@tasks.loop(minutes=60)
async def news_loop():
    await fetch_and_post_news()

@tasks.loop(time=SCHEDULED_VOTE_TIME)
async def daily_vote_loop():
    try:
        channel = await bot.fetch_channel(VOTE_CHANNEL_ID)
        
        # ---[★ 추가: 오늘 날짜 계산 (한국 시간 기준)] ---
        today = datetime.datetime.now(KST)
        date_str = today.strftime("%Y년 %m월 %d일")
        # -----------------------------------------------

        poll = discord.Poll(
            # ★ 수정: 질문에 계산된 날짜(date_str) 삽입
            question=f"🎮 {date_str} 오늘 게임하실 건가요? (포지션 선택)",
            duration=timedelta(hours=11)
        )
        
        poll.add_answer(text="TOP", emoji="🛡️")
        poll.add_answer(text="JGL", emoji="⚔️")
        poll.add_answer(text="MID", emoji="🔥")
        poll.add_answer(text="SUP", emoji="✨")
        poll.add_answer(text="ADC", emoji="🏹")
        poll.add_answer(text="미정", emoji="❓")
        poll.add_answer(text="불참", emoji="❌")

        await channel.send(poll=poll)
        log(f"매일 오후 1시 자동 투표 게시 완료 ({date_str})")
        
    except Exception as e:
        log(f"투표 게시 에러: {e}")
        traceback.print_exc()

@bot.event
async def on_ready():
    log(f"봇 로그인 성공: {bot.user.name}")
    
    # 루프들이 실행 중이지 않으면 시작
    if not news_loop.is_running():
        news_loop.start()
    if not daily_vote_loop.is_running():
        daily_vote_loop.start()

# ================= [ 명령어 구역: 티어 인증 ] =================
@bot.command()
async def 인증(ctx, *, summoner_name=None):
    if not summoner_name or "#" not in summoner_name:
        await ctx.send("❌ 소환사명 뒤에 태그(#)를 포함해 주세요. (예: `!인증 Hide on bush#KR1`)")
        return
        
    target_icon = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": summoner_name, "icon": target_icon}
    icon_url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{target_icon}.png"
    
    embed = discord.Embed(
        title="🛡️ 롤 계정 소유권 인증", 
        description=f"**{summoner_name}**님, 롤 클라이언트에서 위 아이콘으로 변경 후 `!확인`을 입력하세요.", 
        color=0x5865F2
    )
    embed.set_thumbnail(url=icon_url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    if ctx.author.id not in pending_users:
        await ctx.send("❌ 진행 중인 인증이 없습니다. 먼저 `!인증 소환사명#태그`를 시도하세요.")
        return
        
    user_info = pending_users[ctx.author.id]
    name, tag = user_info["name"].split("#", 1) # 태그 파싱 시 안전성 추가
    name = name.strip()
    tag = tag.strip()
    
    try:
        # Riot ID로 PUUID 가져오기
        acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
        async with bot.session.get(acc_url) as r1:
            if r1.status != 200:
                await ctx.send("❌ 라이엇 계정을 찾을 수 없습니다. 이름과 태그를 확인해주세요.")
                return
            acc_data = await r1.json()
            puuid = acc_data.get('puuid')
            
        # PUUID로 소환사 정보(아이콘) 가져오기
        sum_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
        async with bot.session.get(sum_url) as r2:
            if r2.status != 200:
                await ctx.send("❌ 소환사 정보를 불러오지 못했습니다.")
                return
            sum_data = await r2.json()
            current_icon = sum_data.get('profileIconId')
            
        if current_icon == user_info["icon"]:
            await ctx.send(f"✅ 인증 성공! 이제 `!갱신 {user_info['name']}`을 입력하여 티어 역할을 받으세요.")
            del pending_users[ctx.author.id]
        else:
            await ctx.send(f"❌ 아이콘이 아직 변경되지 않았습니다. (현재: {current_icon} / 목표: {user_info['icon']})")
            
    except Exception as e:
        log(f"인증 확인 중 에러: {e}")
        await ctx.send("서버 통신 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

@bot.command()
async def 갱신(ctx, *, summoner_name=None):
    if not summoner_name or "#" not in summoner_name:
        await ctx.send("❌ 태그를 포함해주세요. (예: `!갱신 Hide on bush#KR1`)")
        return
        
    name, tag = summoner_name.split("#", 1)
    name = name.strip()
    tag = tag.strip()
    
    try:
        # PUUID 조회
        acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
        async with bot.session.get(acc_url) as r1:
            if r1.status != 200:
                await ctx.send("❌ 계정을 찾을 수 없습니다.")
                return
            puuid = (await r1.json()).get('puuid')
            
        # 랭크 정보 조회
        league_url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
        async with bot.session.get(league_url) as r2:
            league_data = await r2.json()
            
            user_tier = "UNRANKED"
            for entry in league_data:
                if entry.get('queueType') == 'RANKED_SOLO_5x5':
                    user_tier = entry.get('tier', 'UNRANKED')
                    break
                    
            role_name = user_tier.capitalize()
            new_role = discord.utils.get(ctx.guild.roles, name=role_name)
            
            if not new_role:
                await ctx.send(f"❌ 디스코드 서버에 '{role_name}' 역할이 생성되어 있지 않습니다. 관리자에게 문의하세요.")
                return
                
            # 기존 티어 역할 제거 후 새 역할 부여
            roles_to_remove =[r for r in ctx.author.roles if r.name in TIER_LIST]
            if roles_to_remove:
                await ctx.author.remove_roles(*roles_to_remove)
            
            await ctx.author.add_roles(new_role)
            await ctx.send(f"🔄 **{ctx.author.display_name}**님의 티어가 **{user_tier}**(으)로 갱신되었습니다!")
            
    except Exception as e:
        log(f"갱신 중 에러: {e}")
        await ctx.send("티어 갱신 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

# 봇 실행
bot.run(DISCORD_TOKEN)
