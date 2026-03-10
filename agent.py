from livekit.agents import cli, WorkerOptions


async def entrypoint(ctx):
    pass


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="accountability-buddy"))
