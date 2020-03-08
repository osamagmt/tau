from discord import Embed, File
from discord.ext import commands

import perms

class Manual(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(cls=perms.Lock, name='ids', aliases=[], usage='ids')
    async def ids(self, ctx):
        desc = ('Discord allows users to obtain IDs through the user interface. '
                'Users, servers, channels, messages, roles, etc. all have a unique ID '
                'generated by Discord. They are stored as a 64-bit unsigned integer.\n\n'
                'To obtain an ID from an object in Discord, you must enable developer '
                'mode. Go to User Settings > Appearance > Advanced and toggle on '
                '"Developer Mode." By right clicking any user, server, etc. '
                'there is now a "Copy ID" item in the context menu.')
        embed = Embed(description=desc.replace(' '*8, ''))
        embed.set_image(url='attachment://unknown.png')

        await ctx.send(file=File('assets/devmode.png', 'unknown.png'), embed=embed)

def setup(bot):
    bot.add_cog(Manual(bot))