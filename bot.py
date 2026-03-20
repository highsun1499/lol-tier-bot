import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
import os
import re
import traceback
import random

# [로그 설정] 깃허브 액션에서 즉시 확인 가능하도록 설정
def log(message):
    print(f"--- [확인용 로그] {message} ---", flush=True)

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# 뉴스 채널 ID (정수형으로 확실히 설정)
NEWS_CHANNEL_ID = 1480944831600656384  

# 티어별 색상 설정
TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())

# 인증 대기 유저 저장소
pending_users = {}
# ===============================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# --- [ 뉴스 크롤링 핵심 함수 ] ---
async def fetch_and_post_news():
    log("롤 공식 홈페이지 뉴스 체크 시작...")
    # 목록 페이지보다는 전체 데이터를 담고 있을 가능성이 높은 메인 뉴스 주소
    news_url = "https://www.leagueoflegends.com/ko-kr/news/" 
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            channel = await bot.fetch_channel(int(NEWS_CHANNEL_ID))
            
            async with session.get(news_url) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # [수정된 탐색 로직] 
                    # 1. 모든 'a' 태그 중 href에 '/news/'가 포함된 것들을 수집
                    all_links = soup.find_all('h2') # 보통 뉴스 제목은 h2에 있음
                    articles_data = []

                    for h2 in all_links:
                        # h2의 부모 중 가장 가까운 a 태그 찾기
                        parent_a = h2.find_parent('a', href=True)
                        if parent_a and '/news/' in parent_a['href']:
                            link = parent_a['href']
                            full_link = link if link.startswith('http') else "https://www.leagueoflegends.com" + link
                            title = h2.text.strip()
                            
                            # 이미지 찾기 (부모 a 태그 안에서 img 태그 탐색)
                            img_tag = parent_a.find('img')
                            img_url = img_tag.get('src') if img_tag else ""
                            
                            # 중복 방지 (리스트에 제목이 같으면 패스)
                            if not any(d['link'] == full_link for d in articles_data):
                                articles_data.append({
                                    'link': full_link,
                                    'title': title,
                                    'image': img_url
                                })

                    log(f"홈페이지에서 추출된 뉴스 후보: {len(articles_data)}개")
                    
                    if not articles_data:
                        log("뉴스 데이터를 추출하지 못했습니다. 구조가 완전히 바뀌었을 수 있습니다.")
                        return

                    # 최신 5개 추출 및 역순 정렬
                    target_articles = articles_data[:5]
                    target_articles.reverse()

                    # 채널 히스토리 확인
                    already_posted_links = []
                    async for msg in channel.history(limit=20):
                        if msg.author == bot.user and msg.embeds:
                            already_posted_links.append(msg.embeds[0].url)

                    new_count = 0
                    for art in target_articles:
                        if art['link'] in already_posted_links:
                            continue

                        embed = discord.Embed(
                            title=art['title'],
                            url=art['link'],
                            description="리그 오브 레전드 최신 소식",
                            color=0x00FF99
                        )
                        if art['image']:
                            embed.set_image(url=art['image'])
                        embed.set_footer(text="출처 : LoL 공식 홈페이지")

                        await channel.send(embed=embed)
                        new_count += 1
                        log(f"신규 뉴스 포스팅: {art['title']}")

                    if new_count == 0:
                        log("새로운 소식이 없습니다.")
                else:
                    log(f"홈페이지 접근 실패: {response.status}")
        except Exception as e:
            log(f"뉴스 크롤링 에러 발생: {e}")
            traceback.print_exc()
            
@tasks.loop(minutes=60)
async def news_loop():
    await fetch_and_post_news()

@bot.event
async def on_ready():
    log(f"봇 로그인 성공: {bot.user.name}")
    # 봇 시작 시 바로 뉴스 체크 실행
    await fetch_and_post_news()
    if not news_loop.is_running():
        news_loop.start()

# --- [ 티어 인증 시스템 ] ---
@bot.command()
async def 인증(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ 소환사명 뒤에 태그(#)를 포함해 주세요. (예: 페이커#KR1)")
        return
    
    target_icon = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": summoner_name, "icon": target_icon}
    
    icon_url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{target_icon}.png"
    embed = discord.Embed(
        title="🛡️ 롤 계정 소유권 인증", 
        description=f"**{summoner_name}**님, 프로필 아이콘을 아래 이미지로 변경한 후 `!확인`을 입력하세요.", 
        color=0x5865F2
    )
    embed.set_thumbnail(url=icon_url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    if ctx.author.id not in pending_users:
        await ctx.send("먼저 `!인증 소환사명#태그`를 입력하세요.")
        return

    user_info = pending_users[ctx.author.id]
    name, tag = user_info["name"].split("#")
    
    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                if r1.status != 200:
                    await ctx.send("❌ 라이엇 계정을 찾을 수 없습니다.")
                    return
                puuid = (await r1.json()).get('puuid')

            sum_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(sum_url) as r2:
                current_icon = (await r2.json()).get('profileIconId')

            if current_icon == user_info["icon"]:
                await ctx.send(f"✅ 인증 성공! 이제 `!갱신 {user_info['name']}`을 입력하세요.")
                del pending_users[ctx.author.id]
            else:
                await ctx.send(f"❌ 아이콘이 다릅니다. (현재 ID: {current_icon} / 목표 ID: {user_info['icon']})")
        except Exception:
            await ctx.send("인증 과정 중 오류가 발생했습니다.")

@bot.command()
async def 갱신(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ `!갱신 소환사명#태그`로 입력해주세요.")
        return

    name, tag = summoner_name.split("#")
    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                puuid = (await r1.json()).get('puuid')
            
            league_url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(league_url) as r2:
                league_data = await r2.json()
                
                user_tier = "UNRANKED"
                for entry in league_data:
                    if entry['queueType'] == 'RANKED_SOLO_5x5':
                        user_tier = entry['tier']
                        break
                
                role_name = user_tier.capitalize()
                new_role = discord.utils.get(ctx.guild.roles, name=role_name)

                if not new_role:
                    await ctx.send(f"❌ 서버에 '{role_name}' 역할이 없습니다.")
                    return

                try:
                    roles_to_remove = [r for r in ctx.author.roles if r.name in TIER_LIST]
                    await ctx.author.remove_roles(*roles_to_remove)
                    await ctx.author.add_roles(new_role)
                    await ctx.send(f"🔄 **{user_tier}** 티어 갱신 완료!")
                except discord.Forbidden:
                    await ctx.send("❌ 권한 부족! 봇 역할을 서버 설정 상단으로 올려주세요.")
        except Exception:
            await ctx.send("데이터 갱신 중 오류가 발생했습니다.")

bot.run(DISCORD_TOKEN)
