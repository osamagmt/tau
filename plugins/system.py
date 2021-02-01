import asyncio
import datetime
import os
import platform
import sys
import time

import psutil
import discord
from discord import Embed, File
from discord.ext import commands
from discord.ext.commands import command, guild_only
from discord.utils import find, escape_markdown

import config
import ccp
import utils

class System(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        ccp.event(f'{guild} ({guild.owner})', event='GUILD_ADD')

        await self.bot.guilds_.insert(guild.id)
        if guild.system_channel:
            await self.bot.guilds_.update(guild.id, 'system_channel', guild.system_channel.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        ccp.event(f'{guild} ({guild.owner})', event='GUILD_REM')

        async with self.bot.pool.acquire() as con:
            await self.bot.guilds_.delete(guild.id)
            await con.execute('DELETE FROM members WHERE guild_id = $1', guild.id)
            await con.execute('DELETE FROM role_menus WHERE guild_id = $1', guild.id)
            await con.execute('DELETE FROM ranks WHERE guild_id = $1', guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        ccp.event(f'{member} has joined {member.guild}', event='MEMBER_ADD')

        if member.bot:
            return

        cache = self.bot.guilds_
        guild = member.guild
        if cache[guild.id]['welcome_messages'] and (chan := guild.get_channel(cache[guild.id]['system_channel'])):
            user = escape_markdown(str(member))
            name = escape_markdown(member.display_name)
            server = escape_markdown(guild.name)
            msg = cache[guild.id]['welcome_message'].replace('@user', user).replace('@name', name).replace('@server', server)
            embed = Embed(description=msg, color=utils.Color.green)
            embed.set_author(name=member, icon_url=member.avatar_url)
            embed.set_footer(text='Join', icon_url='attachment://unknown.png')
            embed.timestamp = datetime.datetime.utcnow()

            await chan.send(member.mention, file=File('assets/join.png', 'unknown.png'), embed=embed)

        if self.bot.members.get((member.id, guild.id)):
            muted = self.bot.members[member.id, guild.id]['muted']
            if muted and muted > datetime.datetime.utcnow():
                mute_role = member.guild.get_role(self.bot.guilds_[guild.id]['mute_role'])
                if mute_role:
                    await member.add_roles(mute_role)
            else:
                muted = None
                async with self.bot.pool.acquire() as con:
                    query = 'UPDATE members SET muted = $1 WHERE user_id = $2 AND guild_id = $3'
                    await con.execute(query, None, member.id, guild.id)

        autorole = self.bot.guilds_[member.guild.id]['autorole']
        if role := guild.get_role(autorole):
            await member.add_roles(role)

        if self.bot.ranks.get(guild.id) and (role_ids := self.bot.ranks[guild.id]['role_ids']):
            if self.bot.ranks[guild.id]['levels'][0] == 0 and (role := guild.get_role(role_ids[0])):
                await member.add_roles(role)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        try:
            await member.guild.fetch_ban(member)
        except (discord.Forbidden, discord.NotFound):
            pass
        else:
            return

        ccp.event(f'{member} has left {member.guild}', event='MEMBER_REM')

        if member.bot:
            return

        cache = self.bot.guilds_
        guild = member.guild
        if cache[guild.id]['goodbye_messages'] and (chan := member.guild.get_channel(cache[guild.id]['system_channel'])):
            user = escape_markdown(str(member))
            name = escape_markdown(member.display_name)
            server = escape_markdown(guild.name)
            msg = cache[guild.id]['goodbye_message'].replace('@user', user).replace('@name', name).replace('@server', server)
            embed = Embed(description=msg, color=utils.Color.red)
            embed.set_author(name=member, icon_url=member.avatar_url)
            embed.set_footer(text='Leave', icon_url='attachment://unknown.png')
            embed.timestamp = datetime.datetime.utcnow()

            await chan.send(file=File('assets/leave.png', 'unknown.png'), embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        ccp.event(f'{user} was banned from {guild}', event='MEMBER_BAN')

        if user.bot:
            return

        if self.bot.members.get((user.id, guild.id)):
            await self.bot.members.delete((user.id, guild.id))

    @command(name='config', aliases=['conf'], usage='config')
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.bot_has_permissions(add_reactions=True, external_emojis=True, manage_messages=True)
    @guild_only()
    async def config(self, ctx):
        '''Display or modify guild configuration.
        Config keys can be modified using the reaction menu.
        To select a key, enter the number that corresponds with that key.
        From there, enter the new value. Alternatively, typing \'reset\' will return the key back to its default value.
        Only one user may modify the config at a time to avoid input conflict.
        Note that command cannot be invoked while modifying the config.

        **`welcome_message`** and **`goodbye_message`** are dynamic. The following directives will be replaced automatically:
        **@name** - The user's display name.
        **@user** - The user's name and tag (name#0000 format).
        **@server** - The server's name.

        Toggles will be immediately modified on selection.\n
        **Example:```yml\n♤config```**
        '''
        guild_id = ctx.guild.id
        default = self.bot.guilds_.default

        fields = {}
        toggles = {}
        def build():
            for k, v in self.bot.guilds_[guild_id].items():
                if k == 'log_channel':
                    continue

                if isinstance(default[k], bool):
                    toggles[k] = v
                elif 'role' in k:
                    role = ctx.guild.get_role(v)
                    fields[k] = f'{str(role)} ({role.id})' if role else None
                elif 'channel' in k:
                    ch = ctx.guild.get_channel(v)
                    fields[k] = f'{str(ch)} ({ch.id})' if ch else None
                else:
                    fields[k] = v

            i = 1
            ilen = len(str(len(default))) + 1
            klen = len(max(fields.keys(), key=len))
            conf = ''
            for k, v in fields.items():
                align = ' ' * (klen-len(k))
                pad = ' ' * (ilen-len(str(i)))
                conf += f'{i}.{pad}{k}:{align} {v}\n'
                i += 1

            klen = len(max(toggles.keys(), key=len))
            conf2 = ''
            for k, v in toggles.items():
                tog = utils.Emoji.on if v else utils.Emoji.off
                align = ' ' * (klen-len(k))
                pad = ' ' * (ilen-len(str(i)))
                conf2 += f'` {i}.{pad}{k}{align}` {tog}\n'
                i += 1

            return f'**```groovy\n{conf}```{conf2}**'

        desc = build()
        embed = Embed(description=desc.strip('\n'))
        embed.set_author(name=ctx.guild.name, icon_url='attachment://unknown.png')
        x = '❌'
        menu = await ctx.send(file=File('assets/gear.png', 'unknown.png'), embed=embed)
        await menu.add_reaction(utils.Emoji.settings)

        while True:
            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=90, check=lambda reaction, user: str(reaction.emoji) == utils.Emoji.settings and reaction.message.id == menu.id and not user.bot)
                self.bot.suppressed[ctx.author] = menu.channel

                if user != ctx.author:
                    await reaction.remove(user)
                    continue

                await menu.clear_reactions()
                embed.description += '\n**\\*Please select a config key to edit.**'
                await menu.edit(embed=embed)
                await menu.add_reaction(x)

                while True:
                    on_msg = asyncio.create_task(self.bot.wait_for('message', check=lambda msg: msg.author == user))
                    on_react = asyncio.create_task(self.bot.wait_for('reaction_add', check=lambda reaction, user: str(reaction.emoji) == x and reaction.message.id == menu.id and not user.bot))
                    done, pending = await asyncio.wait([on_msg, on_react], timeout=90, return_when=asyncio.FIRST_COMPLETED)

                    for future in pending:
                        future.cancel()

                    if on_msg in done:
                        msg = done.pop().result()
                        await msg.delete()

                        if msg.channel != menu.channel:
                            raise asyncio.TimeoutError

                        n = int(msg.content) if msg.content.isdigit() else 0
                        if not 0 < n <= len(default):
                            await ctx.send(f'{ctx.author.mention} Sorry! Your input is not valid. Response must be an integer matching a value above.', delete_after=5)
                            continue

                        n -= 1
                        key = tuple(fields.keys())[n] if n < len(fields) else tuple(toggles.keys())[n-len(fields)]
                        if key in fields.keys():
                            embed.description = desc + f'\n**\\*Modifying Current Buffer: `{key}`**'
                            embed.set_footer(text='\'reset\' to return to default')
                            await menu.edit(embed=embed)

                            on_msg = asyncio.create_task(self.bot.wait_for('message', check=lambda msg: msg.author == user))
                            on_react = asyncio.create_task(self.bot.wait_for('reaction_add', check=lambda reaction, user: str(reaction.emoji) == x and reaction.message == menu and not user.bot))
                            done, pending = await asyncio.wait([on_msg, on_react], timeout=90, return_when=asyncio.FIRST_COMPLETED)

                            for future in pending:
                                future.cancel()

                            if on_msg in done:
                                msg = done.pop().result()
                                await msg.delete()

                                if msg.channel != menu.channel:
                                    embed.set_footer(text='')
                                    raise asyncio.TimeoutError

                                if msg.content == 'reset':
                                    val = default[key]
                                    await self.bot.guilds_.update(ctx.guild.id, key, default[key])
                                elif 'role' in key:
                                    role = find(lambda r: r.name == msg.content, ctx.guild.roles)
                                    if not role:
                                        role_id = int(msg.content) if msg.content.isdigit() else 0
                                        role = ctx.guild.get_role(role_id)

                                    if not role:
                                        await ctx.send(f'{ctx.author.mention} Sorry! A role named \'{msg.content}\' could not be found.', delete_after=5)
                                    else:
                                        await self.bot.guilds_.update(ctx.guild.id, key, role.id)
                                elif 'channel' in key:
                                    ch = find(lambda ch: ch.name == msg.content, ctx.guild.text_channels)
                                    if not ch:
                                        ch_id = int(msg.content) if msg.content.isdigit() else 0
                                        ch = ctx.guild.get_channel(ch_id)

                                    if not ch:
                                        await ctx.send(f'{ctx.author.mention} Sorry! A channel named \'{msg.content}\' could not be found.', delete_after=5)
                                    else:
                                        await self.bot.guilds_.update(ctx.guild.id, key, ch.id)
                                elif 'quantity' in key:
                                    qty = int(msg.content) if msg.content.isdigit() else 0
                                    if not 0 < qty < 256:
                                        await ctx.send(f'{ctx.author.mention} `star_quantity` must be an integer between 1 and 255.', delete_after=5)
                                    else:
                                        await self.bot.guilds_.update(ctx.guild.id, key, qty)
                                else:
                                    await self.bot.guilds_.update(ctx.guild.id, key, msg.content)

                                desc = build()
                                embed.description = desc + '\n**\\*Please select a config key to edit.**'
                                embed.set_footer(text='')
                                await menu.edit(embed=embed)
                            elif on_react in done:
                                reaction, reactor = done.pop().result()
                                if reactor != user:
                                    await reaction.remove(reactor)
                                    continue

                                raise asyncio.TimeoutError
                            else:
                                raise asyncio.TimeoutError
                        else:
                            val = True if not self.bot.guilds_[ctx.guild.id][key] else False
                            await self.bot.guilds_.update(ctx.guild.id, key, val)

                            desc = build()
                            embed.description = desc + '\n**\\*Please select a config key to edit.**'
                            await menu.edit(embed=embed)
                    elif on_react in done:
                        reaction, reactor = done.pop().result()
                        if reactor != user:
                            await reaction.remove(reactor)
                            continue

                        raise asyncio.TimeoutError
                    else:
                        raise asyncio.TimeoutError
            except asyncio.TimeoutError:
                await menu.clear_reactions()
                if embed.description != desc.strip('\n'):
                    embed.description = desc.strip('\n')
                    await menu.edit(embed=embed)
            finally:
                try:
                    del self.bot.suppressed[ctx.author]
                except:
                    pass
                break

    @command(name='reload', aliases=['r'], usage='reload <ext>')
    @commands.is_owner()
    async def reload(self, ctx, ext):
        '''Reload an extension.
        `ext` must be the dot path to the extension relative to the entry point of the app.\n
        **Example:```yml\n♤reload system```**
        '''
        try:
            self.bot.reload_extension(f'plugins.{ext}')

            desc = f'plugins.{ext} has been reloaded.'
            color = utils.Color.green
            color_name = 'green'
        except (commands.ExtensionFailed, commands.ExtensionNotLoaded, commands.NoEntryPointError) as err:
            desc = err
            color = utils.Color.red
            color_name = 'red'

        embed = Embed(color=color)
        embed.set_author(name=desc, icon_url='attachment://unknown.png')

        await ctx.reply(file=File(f'assets/{color_name}dot.png', 'unknown.png'), embed=embed, mention_author=False)

    @command(name='remove', aliases=['leave', 'rem'], usage='remove [id]')
    @commands.is_owner()
    async def remove(self, ctx, id: int):
        '''Remove a server.\n
        If an ID is not given, the current server will be removed.
        **Example:```yml\n♤remove 546397670793805825\n♤leave```**
        '''
        guild = self.bot.get_guild(id)
        if not guild:
            guild = ctx.guild

        await guild.leave()

        if ctx.guild != guild:
            desc = f'**```diff\n- {guild.name} has been removed```**'
            embed = Embed(description=desc, color=utils.Color.red)

            await ctx.send(embed=embed)

    @command(name='rule', usage='rule <index>')
    @commands.bot_has_permissions(external_emojis=True, mention_everyone=True)
    @guild_only()
    async def rule(self, ctx, index: int):
        '''Cite a rule.\n
        **Example:```yml\n♤rule 1```**
        '''
        rule = self.bot.rules.get((ctx.guild.id, index), {'rule': ''})

        if not rule['rule']:
            return await ctx.send(f'{ctx.author.mention} Rule with index **`{index}`** could not be found.', delete_after=5)

        embed = Embed(title='Rules', description=f'**{index}.** {rule["rule"]}')

        await ctx.send(embed=embed)

    @command(name='rules', usage='rules')
    @commands.bot_has_permissions(external_emojis=True, mention_everyone=True)
    @guild_only()
    async def rules(self, ctx):
        '''Display the rules.\n
        **Example:```yml\n♤rules```**
        '''
        async with self.bot.pool.acquire() as con:
            rules = await con.fetch('SELECT index_, rule FROM rules WHERE guild_id = $1 ORDER BY index_ ASC', ctx.guild.id)

        if not rules:
            return await ctx.send(f'{ctx.author.mention} Rules have not been initialized.', delete_after=5)

        board = ''
        for index, rule in rules:
            board += f'**{index}.** {rule}\n'

        embed = Embed(title='Rules', description=board)

        await ctx.send(embed=embed)

    @command(name='setrule', usage='setrule <index> [rule]')
    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_permissions(external_emojis=True, mention_everyone=True)
    @guild_only()
    async def setrule(self, ctx, index: int, *, rule: str = ''):
        '''Set a rule.
        `index` must be a positive integer no larger than 255.
        Leave `rule` blank to reset.\n
        **Example:```yml\n♤setrule 1 Do not spam```**
        '''
        if not 0 <= index < 256:
            raise commands.BadArgument

        if rule:
            await self.bot.rules.update((ctx.guild.id, index), 'rule', rule)
        else:
            await self.bot.rules.delete((ctx.guild.id, index))

        embed = Embed(title='Rules')

        if rule:
            desc = f'**{index}.** {rule}'
        else:
            desc = f'**```yml\n+ Rule {index} has been reset```**'
            embed.colour = utils.Color.green

        embed.description = desc

        await ctx.send(embed=embed)

    @command(name='shutdown', aliases=['exit', 'quit', 'kill'], usage='shutdown')
    @commands.is_owner()
    async def shutdown(self, ctx):
        '''Shut down the bot.\n
        **Example:```yml\n♤shutdown\n♤exit```**
        '''
        files = [File('assets/reddot.png', 'unknown.png'), File('assets/redsplash.png', 'unknown1.png')]
        embed = Embed(color=utils.Color.red)
        embed.set_author(name='Shutting down...', icon_url='attachment://unknown.png')
        embed.set_image(url='attachment://unknown1.png')

        await ctx.reply(files=files, embed=embed, mention_author=False)

        await self.bot.pool.close()
        await self.bot.close()

        os._exit(0)

def setup(bot):
    bot.add_cog(System(bot))
