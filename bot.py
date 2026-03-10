import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
import random
import traceback
import os
import asyncio

# [긴급 수정] 로그가 깃허브 액션 화면에 즉시 나타나게 합니다.
def log(message):
    print(f"--- [확인용 로그] {message} ---", flush=True)

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# 📢 새소식을 보낼 채널 ID (반드시 본인 서버의 채널 ID로 수정)
NEWS_CHANNEL_ID = 1480944831600656384

TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())
# ===============================================

# [개선] Intents를 필요한 것만 켜서 봇의 부하를 줄입니다.
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# --- [ 뉴스 크롤링 핵심 함수 ] ---
async def fetch_and_post_news():
    log("뉴스 크롤링 시도 중...")
    url = "https://www.leagueoflegends.com/ko-kr/news/" # 주소 변경
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with aiohttp.ClientSession(headers=headers) as session: # 헤더 추가
        try:
            channel = await bot.fetch_channel(NEWS_CHANNEL_ID)
            async with session.get(url) as response:
                log(f"접속 시도 결과: {response.status}") # 상태 코드 확인용
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    articles = soup.select('a[data-testid^="article-card-"]')[:5]
                    articles.reverse() 

                    # 이미 올린 뉴스인지 채널 메시지 확인
                    already_posted = []
                    async for msg in channel.history(limit=20):
                        if msg.author == bot.user and msg.embeds:
                            already_posted.append(msg.embeds[0].description.replace("**", "").strip())

                    new_count = 0
                    for art in articles:
                        title = art.find('h2').text.strip()
                        link = "https://www.leagueoflegends.com" + art['href']
                        
                        if title in already_posted:
                            continue
                        
                        embed = discord.Embed(title="🆕 롤 최신 소식", description=f"**{title}**", url=link, color=0x0066ff)
                        await channel.send(embed=embed)
                        new_count += 1
                        log(f"뉴스 전송 완료: {title}")
                    
                    if new_count == 0:
                        log("새로운 뉴스가 없습니다.")
                else:
                    log(f"홈페이지 접속 실패 (상태 코드: {response.status})")
        except Exception as e:
            log(f"뉴스 처리 중 에러: {e}")

@tasks.loop(minutes=60)
async def news_loop():
    await fetch_and_post_news()

@bot.event
async def on_ready():
    log(f"봇 로그인 성공: {bot.user.name}")
    
    # [핵심] 봇이 켜지자마자 루프 기다리지 않고 '즉시' 한 번 실행!
    await fetch_and_post_news()
    
    if not news_loop.is_running():
        news_loop.start()
        log("정기 뉴스 체크 루프 가동 시작 (60분 간격)")

# --- [ 기존 기능: 서버 입장 시 역할 자동 생성 ] ---
@bot.event
async def on_guild_join(guild):
    print(f"--- [로그] 새로운 서버 입장: {guild.name} ---")
    for role_name, color_hex in TIER_DATA.items():
        if not discord.utils.get(guild.roles, name=role_name):
            try:
                await guild.create_role(name=role_name, color=discord.Color(color_hex), hoist=True)
            except discord.Forbidden:
                print(f"--- [로그] {guild.name} 서버: 역할 생성 권한 없음 ---")
                break

# --- [ 기존 기능: 인증 및 갱신 ] ---
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
        description=f"**{summoner_name}**님, 본인 확인을 위해\n프로필 아이콘을 아래 이미지로 변경한 후 `!확인`을 입력하세요.", 
        color=0x5865F2
    )
    embed.set_thumbnail(url=icon_url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    if ctx.author.id not in pending_users:
        await ctx.send("먼저 `!인증 소환사명#태그`를 입력해 인증을 시작하세요.")
        return

    user_info = pending_users[ctx.author.id]
    name, tag = user_info["name"].split("#")
    
    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                acc_data = await r1.json()
                puuid = acc_data.get('puuid')
                if not puuid:
                    await ctx.send("❌ 라이엇 계정 정보를 찾을 수 없습니다.")
                    return

            sum_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(sum_url) as r2:
                sum_data = await r2.json()
                current_icon = sum_data.get('profileIconId')

            if current_icon == user_info["icon"]:
                await ctx.send(f"✅ **{user_info['name']}**님, 인증 성공!\n이제 `!갱신 {user_info['name']}`을 입력하세요.")
                del pending_users[ctx.author.id]
            else:
                await ctx.send(f"❌ 아이콘 불일치. (현재: {current_icon} / 목표: {user_info['icon']})")
        except Exception:
            await ctx.send("오류 발생. API 키를 확인하세요.")

@bot.command()
async def 갱신(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ `!갱신 소환사명#태그` 형식으로 입력해주세요.")
        return

    name, tag = summoner_name.split("#")
    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                acc_data = await r1.json()
                puuid = acc_data.get('puuid')
            
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
                    await ctx.send(f"❌ '{role_name}' 역할이 서버에 없습니다.")
                    return

                # [개선] 권한 에러 방지를 위한 예외 처리 추가
                try:
                    # 기존 티어 역할 제거
                    roles_to_remove = [r for r in ctx.author.roles if r.name in TIER_LIST]
                    if roles_to_remove:
                        await ctx.author.remove_roles(*roles_to_remove)
                    
                    # 새 역할 부여
                    await ctx.author.add_roles(new_role)
                    await ctx.send(f"🔄 **{user_tier}** 역할 부여 완료!")
                except discord.Forbidden:
                    await ctx.send("❌ 봇의 권한이 부족합니다. 서버 설정에서 **봇의 역할 순위를 티어 역할보다 위로** 올려주세요!")

        except Exception:
            await ctx.send("갱신 중 오류가 발생했습니다.")

bot.run(DISCORD_TOKEN)
