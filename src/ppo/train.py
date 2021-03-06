import os
import argparse
import torch
import uuid
import glob
from termcolor import colored
from pyvirtualdisplay import Display
from collections import namedtuple

import sys
sys.path.append('..')
from shared.utils.utils import init_uncert_file
from shared.components.env import Env
from shared.utils.replay_buffer import ReplayMemory
from shared.components.logger import Logger
from components.uncert_agents import make_agent
from models import make_model
from components.trainer import Trainer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train a PPO agent for Inverted Double Pendulum",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Environment Config
    env_config = parser.add_argument_group("Environment config")
    env_config.add_argument(
        "-AR", "--action-repeat", type=int, default=1, help="repeat action in N frames"
    )
    env_config.add_argument(
        "-TS",
        "--train-seed",
        type=float,
        default=0,
        help="Train Environment Random seed",
    )
    env_config.add_argument(
        "-ES",
        "--eval-seed",
        type=float,
        default=10,
        help="Evaluation Environment Random seed",
    )
    env_config.add_argument(
        "-N",
        "--noise",
        type=str,
        # default="0,0.1",
        default=None,
        help='Whether to use noise or not, and standard deviation bounds separated by comma (ex. "0,0.5")',
    )

    # Agent Config
    agent_config = parser.add_argument_group("Agent config")
    agent_config.add_argument(
        "-M",
        "--model",
        type=str,
        default="base",
        help='Type of uncertainty model: "base", "sensitivity", "dropout", "bootstrap", "aleatoric", "bnn" or "custom"',
    )
    agent_config.add_argument(
        "-NN",
        "--nb-nets",
        type=int,
        default=10,
        help="Number of networks to estimate uncertainties",
    )
    agent_config.add_argument(
        "-G", "--gamma", type=float, default=0.99, help="discount factor"
    )
    agent_config.add_argument(
        "-SS", "--state-stack", type=int, default=6, help="Number of state stack as observation"
    )
    agent_config.add_argument(
        "-A",
        "--architecture",
        type=str,
        default="1024",
        help='Base network architecture',
    )
    agent_config.add_argument(
        "-PE",
        "--ppo-epoch",
        type=int,
        default=20,
        help='Number of training updates each time buffer is full',
    )
    agent_config.add_argument(
        '-FC',
        '--from-checkpoint', 
        type=str, 
        default=None, 
        help='Path to trained model')
    agent_config.add_argument(
        '-CP',
        '--clip-param', 
        type=float, 
        default=0.1, 
        help='Clip Parameter')

    # Training Config
    train_config = parser.add_argument_group("Train config")
    train_config.add_argument(
        "-E", "--episodes", type=int, default=50000, help="Number of training episode"
    )
    train_config.add_argument(
        "-D",
        "--device",
        type=str,
        default="auto",
        help='Which device use: "cpu" or "cuda", "auto" for autodetect',
    )
    train_config.add_argument(
        "-EI",
        "--eval-interval",
        type=int,
        default=20,
        help="Interval between evaluations",
    )
    train_config.add_argument(
        "-EV",
        "--evaluations",
        type=int,
        default=3,
        help="Number of evaluations episodes every x training episode",
    )
    train_config.add_argument(
        "-ER",
        "--eval-render",
        action="store_true",
        help="render the environment on evaluation",
    )
    train_config.add_argument(
        "-DB",
        "--debug",
        action="store_true",
        help="debug mode",
    )

    # Update
    update_config = parser.add_argument_group("Update config")
    update_config.add_argument(
        "-BC", "--buffer-capacity", type=int, default=1000, help="Buffer Capacity"
    )
    update_config.add_argument(
        "-BS", "--batch-size", type=int, default=128, help="Batch Capacity"
    )
    update_config.add_argument(
        "-LR", "--learning-rate", type=float, default=0.001, help="Learning Rate"
    )

    args = parser.parse_args()
    
    run_id = uuid.uuid4()
    # run_name = f"{args.model}_{run_id}"
    run_name = args.model
    render_path = "render"
    render_model_path = f"{render_path}/train"
    train_render_model_path = f"{render_model_path}/{run_name}"
    param_path = "param"
    uncertainties_path = "uncertainties"
    uncertainties_train_path = f"{uncertainties_path}/eval"
    uncertainties_file_path = f"{uncertainties_train_path}/{run_name}.txt"

    print(colored("Initializing data folders", "blue"))
    # Init model checkpoint folder and uncertainties folder
    if not args.debug:
        if not os.path.exists(param_path):
            os.makedirs(param_path)
        if not os.path.exists(uncertainties_path):
            os.makedirs(uncertainties_path)
        if not os.path.exists(render_path):
            os.makedirs(render_path)
        if not os.path.exists(render_model_path):
            os.makedirs(render_model_path)
        if not os.path.exists(train_render_model_path):
            os.makedirs(train_render_model_path)
        else:
            files = glob.glob(f"{train_render_model_path}/*")
            for f in files:
                os.remove(f)
        if not os.path.exists(uncertainties_train_path):
            os.makedirs(uncertainties_train_path)
        init_uncert_file(file=uncertainties_file_path)
    print(colored("Data folders created successfully", "green"))

    # Virtual display
    display = Display(visible=0, size=(1400, 900))
    display.start()

    # Whether to use cuda or cpu
    if args.device == "auto":
        torch.cuda.empty_cache()
        use_cuda = torch.cuda.is_available()
        device = torch.device("cuda" if use_cuda else "cpu")
        torch.manual_seed(args.train_seed)
        if use_cuda:
            torch.cuda.manual_seed(args.train_seed)
    else:
        device = args.device
    print(colored(f"Using: {device}", "green"))

    # Init logger
    logger = Logger("inv-pendulum-ppo", args.model, run_name, str(run_id), args=vars(args))
    config = logger.get_config()

    # Noise parser
    if config["noise"]:
        add_noise = [float(bound) for bound in config["noise"].split(",")]
    else:
        add_noise = None

    # Init Agent and Environment
    print(colored("Initializing agent and environments", "blue"))
    env = Env(
        state_stack=config["state_stack"],
        action_repeat=config["action_repeat"],
        seed=config["train_seed"],
        noise=add_noise,
        done_reward_threshold=-1000
    )
    eval_env = Env(
        state_stack=config["state_stack"],
        action_repeat=config["action_repeat"],
        seed=config["eval_seed"],
        path_render=train_render_model_path if config["eval_render"] else None,
        evaluations=config["evaluations"],
        done_reward_threshold=-1000
    )
    Transition = namedtuple(
        "Transition", ("state", "action", "reward", "next_state", "a_logp")
    )
    buffer = ReplayMemory(
        config["buffer_capacity"],
        config["batch_size"],
        Transition
    )
    architecture = [int(l) for l in config["architecture"].split("-")]
    model = make_model(
        model=config["model"],
        state_stack=config["state_stack"],
        input_dim=env.observation_dims,
        output_dim=env.action_dims,
        architecture=architecture,
    ).to(device)
    agent = make_agent(
        model=model,
        gamma=config["gamma"],
        buffer=buffer,
        logger=logger,
        device=device,
        batch_size=config["batch_size"],
        lr=config["learning_rate"],
        nb_nets=config["nb_nets"],
        ppo_epoch=config["ppo_epoch"],
        clip_param=config["clip_param"]
    )
    init_epoch = 0
    if config["from_checkpoint"]:
        init_epoch = agent.load(config["from_checkpoint"])
    print(colored("Agent and environments created successfully", "green"))

    noise_print = "not using noise"
    if env.use_noise:
        if env.generate_noise:
            noise_print = f"using noise with [{env.noise_lower}, {env.noise_upper}] std bounds"
        else:
            noise_print = f"using noise with [{env.random_noise}] std"

    episodes = config["episodes"]
    print(
        colored(
            f"Training {type(agent)} during {episodes} epochs and {noise_print}",
            "magenta",
        )
    )

    for name, param in config.items():
        print(colored(f"{name}: {param}", "cyan"))

    trainer = Trainer(
        agent,
        env,
        eval_env,
        logger,
        config["episodes"],
        init_ep=init_epoch,
        nb_evaluations=config["evaluations"],
        eval_interval=config["eval_interval"],
        model_name=run_name,
        checkpoint_every=10,
        debug=config["debug"],
    )

    trainer.run()
    env.close()
    eval_env.close()