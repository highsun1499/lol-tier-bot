import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
import os
import re
import traceback
import random
import html
from datetime import time, timezone, timedelta  # 시간 설정을 위해 추가

# [로그 설정]
def log(message):
    print(f"--- [확인용 로그] {message} ---", flush=True)

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWS_CHANNEL_ID = 1480944831600656384  
VOTE_CHANNEL_ID = 1484797598241128598  # 투표가 올라갈 채널 ID (뉴스 채널과 동일하게 설정됨)

# 한국 시간(KST) 오후 1시 설정을 위한 타임존 정의
KST = timezone(timedelta(hours=9))
scheduled_vote_time = time(hour=13, minute=0, second=0, tzinfo=KST)

TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())
pending_users = {}
# ===============================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# --- 뉴스 크롤링 함수 ---
async def fetch_and_post_news():
    log("롤 공식 홈페이지 뉴스 체크 시작...")
    news_url = "https://www.leagueoflegends.com/ko-kr/news/" 
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            channel = await bot.fetch_channel(int(NEWS_CHANNEL_ID))
            async with session.get(news_url) as response:
                if response.status == 200:
                    raw_html = await response.text()
                    soup = BeautifulSoup(raw_html, 'html.parser')
                    
                    articles = soup.select('a[data-testid^="article"]') 
                    log(f"홈페이지에서 찾은 뉴스 개수: {len(articles)}개")
                    
                    if not articles: return

                    target_articles = articles[:10]
                    target_articles.reverse()

                    already_posted_links = []
                    async for msg in channel.history(limit=100):
                        if msg.author == bot.user and msg.embeds:
                            already_posted_links.append(msg.embeds[0].url)

                    for article in target_articles:
                        href = article.get('href', '')
                        link = href if href.startswith('http') else "https://www.leagueoflegends.com" + href
                        
                        if link in already_posted_links: continue

                        title_el = article.find('div', {'data-testid': 'card-title'}) or article.select_one('h2')
                        title = title_el.get_text().strip() if title_el else "새로운 소식"
                        
                        desc_el = article.find('div', {'data-testid': 'card-description'})
                        description = desc_el.get_text().strip() if desc_el else "클릭하여 자세한 내용을 확인하세요."
                        
                        # --- [이미지 추출 로직 수정 구간] ---
                        image_url = ""
                        # 제공해주신 mediaImage 속성을 최우선으로 찾습니다.
                        img_tag = article.find('img', {'data-testid': 'mediaImage'}) or \
                                  article.find('img', {'data-testid': 'banner-image'}) or \
                                  article.find('img')
                        
                        if img_tag:
                            # 1. srcset이 있으면 첫 번째 주소 사용, 없으면 src나 data-src 사용
                            srcset = img_tag.get('srcset')
                            if srcset:
                                raw_src = srcset.split(',')[0].split(' ')[0]
                            else:
                                raw_src = img_tag.get('src') or img_tag.get('data-src') or ""
                            
                            if raw_src:
                                # 2. HTML 특수문자(&amp;) 변환 및 공백 제거
                                image_url = html.unescape(raw_src).strip()
                                
                                # 3. 경로 보정 (이미 http로 시작하면 그대로 둠)
                                if not image_url.startswith('http'):
                                    if image_url.startswith('//'):
                                        image_url = "https:" + image_url
                                    elif image_url.startswith('/'):
                                        image_url = "https://www.leagueoflegends.com" + image_url
                        # --- [이미지 추출 로직 끝] ---

                        embed = discord.Embed(
                            title=title,
                            url=link,
                            description=description,
                            color=0x00FF99
                        )
                        
                        if image_url and image_url.startswith('http'):
                            embed.set_image(url=image_url)
                            log(f"이미지 추출 성공: {title} -> {image_url[:50]}...")
                        else:
                            log(f"이미지 추출 실패(주소 없음): {title}")
                        
                        embed.set_footer(text="새 소식")

                        try:
                            await channel.send(embed=embed)
                            log(f"포스팅 완료: {title}")
                        except Exception as send_error:
                            log(f"전송 실패: {send_error}")
                else:
                    log(f"홈페이지 접근 실패: {response.status}")
        except Exception as e:
            log(f"에러 발생: {e}")
            
# --- 뉴스 체크 루프 (60분마다) ---
@tasks.loop(minutes=60)
async def news_loop():
    await fetch_and_post_news()

# --- 매일 오후 1시 자동 투표 루프 ---
@tasks.loop(time=scheduled_vote_time)
async def daily_vote_loop():
    try:
        channel = await bot.fetch_channel(int(VOTE_CHANNEL_ID))
        
        poll = discord.Poll(
            question="🎮 오늘 게임하실 건가요? (포지션 선택)",
            duration=timedelta(hours=11)  # 약 11시간 동안 투표 유지
        )
        
        # 요청하신 선택지 반영
        poll.add_answer(text="TOP", emoji="🛡️")
        poll.add_answer(text="JGL", emoji="⚔️")
        poll.add_answer(text="MID", emoji="🔥")
        poll.add_answer(text="ADC", emoji="🏹")
        poll.add_answer(text="SUP", emoji="✨")
        poll.add_answer(text="미정", emoji="❓")
        poll.add_answer(text="불참", emoji="❌")

        await channel.send(poll=poll)
        log("매일 오후 1시 자동 투표 게시 완료")
        
    except Exception as e:
        log(f"투표 게시 에러: {e}")
        traceback.print_exc()

@bot.event
async def on_ready():
    log(f"봇 로그인 성공: {bot.user.name}")
    
    # 봇 실행 시 즉시 뉴스 체크 한 번 수행
    await fetch_and_post_news()
    
    # 루프들이 실행 중이지 않으면 시작
    if not news_loop.is_running():
        news_loop.start()
    if not daily_vote_loop.is_running():
        daily_vote_loop.start()

# --- 티어 인증 명령구역 ---
@bot.command()
async def 인증(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ 소환사명 뒤에 태그(#)를 포함해 주세요.")
        return
    target_icon = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": summoner_name, "icon": target_icon}
    icon_url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{target_icon}.png"
    embed = discord.Embed(title="🛡️ 롤 계정 소유권 인증", description=f"**{summoner_name}**님, 아이콘을 변경 후 `!확인`을 입력하세요.", color=0x5865F2)
    embed.set_thumbnail(url=icon_url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    if ctx.author.id not in pending_users:
        await ctx.send("먼저 `!인증`을 시도하세요.")
        return
    user_info = pending_users[ctx.author.id]
    name, tag = user_info["name"].split("#")
    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                if r1.status != 200:
                    await ctx.send("❌ 계정을 찾을 수 없습니다.")
                    return
                puuid = (await r1.json()).get('puuid')
            sum_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(sum_url) as r2:
                current_icon = (await r2.json()).get('profileIconId')
            if current_icon == user_info["icon"]:
                await ctx.send(f"✅ 인증 성공! 이제 `!갱신 {user_info['name']}`을 입력하세요.")
                del pending_users[ctx.author.id]
            else:
                await ctx.send(f"❌ 아이콘 불일치. (현재: {current_icon} / 목표: {user_info['icon']})")
        except:
            await ctx.send("오류가 발생했습니다.")

@bot.command()
async def 갱신(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ 태그를 포함해주세요.")
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
                roles_to_remove = [r for r in ctx.author.roles if r.name in TIER_LIST]
                await ctx.author.remove_roles(*roles_to_remove)
                await ctx.author.add_roles(new_role)
                await ctx.send(f"🔄 **{user_tier}** 티어 갱신 완료!")
        except:
            await ctx.send("갱신 중 오류가 발생했습니다.")

bot.run(DISCORD_TOKEN)
