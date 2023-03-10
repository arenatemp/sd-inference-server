import os
import torch
import utils

from transformers import CLIPTextConfig, CLIPTokenizer
from clip import CustomCLIP
from diffusers import AutoencoderKL, UNet2DConditionModel
from diffusers.models.vae import DiagonalGaussianDistribution
from lora import LoRANetwork
from hypernetwork import Hypernetwork
from unet import UNET as SDUNET

class SDUNET(SDUNET):
    def __init__(self, model_type, prediction_type, dtype):
        self.model_type = model_type
        self.prediction_type = prediction_type
        super().__init__(**SDUNET.get_config(model_type))
        self.to(dtype)
        self.additional = None

    def __getattr__(self, name):
        if name == "device":
            return next(self.parameters()).device
        if name == "dtype":
            return next(self.parameters()).dtype
        return super().__getattr__(name)
        
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']
        model_type = state_dict['metadata']['model_type']
        prediction_type = state_dict['metadata']['prediction_type']

        utils.cast_state_dict(state_dict, dtype)
        
        with utils.DisableInitialization():
            unet = SDUNET(model_type, prediction_type, dtype)
            missing, _ = unet.load_state_dict(state_dict, strict=False)
        if missing:
            raise ValueError("ERROR missing keys: " + ", ".join(missing))

        unet.additional = AdditionalNetworks(unet)
        return unet

    @staticmethod
    def get_config(model_type):
        if model_type == "SDv1":
            config = dict(cross_attention_dim=768, attention_head_dim=[8,8,8,8])
        else:
            config = dict(cross_attention_dim=1024, attention_head_dim=[5,10,20,20])
        return config

class UNET(UNet2DConditionModel):
    def __init__(self, model_type, prediction_type, dtype):
        self.model_type = model_type
        self.prediction_type = prediction_type
        super().__init__(**UNET.get_config(model_type))
        self.to(dtype)
        self.additional = None

    def autocast(self):
        return "cuda" if "cuda" in str(self.device) else "cpu"
        
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']
        model_type = state_dict['metadata']['model_type']
        prediction_type = state_dict['metadata']['prediction_type']

        utils.cast_state_dict(state_dict, dtype)
        
        with utils.DisableInitialization():
            unet = UNET(model_type, prediction_type, dtype)
            missing, _ = unet.load_state_dict(state_dict, strict=False)
        if missing:
            raise ValueError("ERROR missing keys: " + ", ".join(missing))
        
        unet.additional = AdditionalNetworks(unet)
        return unet

    @staticmethod
    def get_config(model_type):
        if model_type == "SDv1":
            config = dict(
                sample_size=32,
                in_channels=4,
                out_channels=4,
                down_block_types=('CrossAttnDownBlock2D', 'CrossAttnDownBlock2D', 'CrossAttnDownBlock2D', 'DownBlock2D'),
                up_block_types=('UpBlock2D', 'CrossAttnUpBlock2D', 'CrossAttnUpBlock2D', 'CrossAttnUpBlock2D'),
                block_out_channels=(320, 640, 1280, 1280),
                layers_per_block=2,
                cross_attention_dim=768,
                attention_head_dim=8,
            )
        elif model_type == "SDv2":
            config = dict(
                sample_size=32,
                in_channels=4,
                out_channels=4,
                down_block_types=('CrossAttnDownBlock2D', 'CrossAttnDownBlock2D', 'CrossAttnDownBlock2D', 'DownBlock2D'),
                up_block_types=('UpBlock2D', 'CrossAttnUpBlock2D', 'CrossAttnUpBlock2D', 'CrossAttnUpBlock2D'),
                block_out_channels=(320, 640, 1280, 1280),
                layers_per_block=2,
                cross_attention_dim=1024,
                attention_head_dim=[5, 10, 20, 20],
                use_linear_projection=True
            )
        else:
            raise ValueError(f"unknown type: {model_type}")
        return config

class VAE(AutoencoderKL):
    def __init__(self, model_type, dtype):
        self.model_type = model_type
        super().__init__(**VAE.get_config(model_type))
        self.enable_slicing()
        self.to(dtype)
    
    def autocast(self):
        return "cuda" if "cuda" in str(self.device) else "cpu"

    class LatentDistribution(DiagonalGaussianDistribution):
        def sample(self, noise):
            x = self.mean + self.std * noise
            return x

    def encode(self, x):
        h = self.encoder(x)
        moments = self.quant_conv(h)
        posterior = VAE.LatentDistribution(moments)
        return posterior
        
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']
        model_type = state_dict['metadata']['model_type']

        utils.cast_state_dict(state_dict, dtype)
        
        with utils.DisableInitialization():
            vae = VAE(model_type, dtype)
            missing, _ = vae.load_state_dict(state_dict, strict=False)
        if missing:
            raise ValueError("missing keys: " + missing)
        return vae

    @staticmethod
    def get_config(model_type):
        if model_type in ["SDv1", "SDv2"]:
            config = dict(
                sample_size=256,
                in_channels=3,
                out_channels=3,
                down_block_types=('DownEncoderBlock2D', 'DownEncoderBlock2D', 'DownEncoderBlock2D', 'DownEncoderBlock2D'),
                up_block_types=('UpDecoderBlock2D', 'UpDecoderBlock2D', 'UpDecoderBlock2D', 'UpDecoderBlock2D'),
                block_out_channels=(128, 256, 512, 512),
                latent_channels=4,
                layers_per_block=2,
            )
        else:
            raise ValueError(f"unknown type: {model_type}")
        return config

class CLIP(CustomCLIP):
    def __init__(self, model_type, dtype):
        self.model_type = model_type
        super().__init__(CLIP.get_config(model_type))
        self.to(dtype)

        self.tokenizer = Tokenizer(model_type)
        self.additional = None
        
    def autocast(self):
        return "cuda" if "cuda" in str(self.device) else "cpu"
        
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']
        model_type = state_dict['metadata']['model_type']

        utils.cast_state_dict(state_dict, dtype)
        
        with utils.DisableInitialization():
            clip = CLIP(model_type, dtype)
            missing, _ = clip.load_state_dict(state_dict, strict=False)
        if missing:
            raise ValueError("missing keys: " + ', '.join(missing))

        clip.additional = AdditionalNetworks(clip)
        return clip

    @staticmethod
    def get_config(model_type):
        if model_type == "SDv1":
            config = CLIPTextConfig(
                attention_dropout=0.0,
                bos_token_id=0,
                dropout=0.0,
                eos_token_id=2,
                hidden_act="quick_gelu",
                hidden_size=768,
                initializer_factor=1.0,
                initializer_range=0.02,
                intermediate_size=3072,
                layer_norm_eps=1e-05,
                max_position_embeddings=77,
                model_type="clip_text_model",
                num_attention_heads=12,
                num_hidden_layers=12,
                pad_token_id=1,
                projection_dim=768,
                transformers_version="4.25.1",
                vocab_size=49408
            )
        elif model_type == "SDv2":
            config = CLIPTextConfig(
                vocab_size=49408,
                hidden_size=1024,
                intermediate_size=4096,
                num_hidden_layers=23,
                num_attention_heads=16,
                max_position_embeddings=77,
                hidden_act="gelu",
                layer_norm_eps=1e-05,
                dropout=0.0,
                attention_dropout=0.0,
                initializer_range=0.02,
                initializer_factor=1.0,
                pad_token_id=1,
                bos_token_id=0,
                eos_token_id=2,
                model_type="clip_text_model",
                projection_dim=512,
                transformers_version="4.25.0.dev0",
            )
        else:
            raise ValueError(f"unknown type: {model_type}")
        return config

    def set_textual_inversions(self, embeddings):
        tokenized = []
        for name, vec in embeddings.items():
            name = tuple(self.tokenizer(name)["input_ids"][1:-1])
            tokenized += [(name, vec)]
        self.textual_inversions = tokenized

class Tokenizer():
    def __init__(self, model_type):
        tokenizer = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tokenizer")
        self.tokenizer = CLIPTokenizer.from_pretrained(tokenizer)
        self.model_type = model_type
        self.bos_token_id = 49406
        self.eos_token_id = 49407
        self.pad_token_id = self.eos_token_id if model_type == "SDv1" else 0
        self.comma_token_id = 267

    def __call__(self, texts):
        return self.tokenizer(texts)

class LoRA(LoRANetwork):
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']

        utils.cast_state_dict(state_dict, dtype)

        model = LoRA(state_dict)
        model.to(dtype)

        return model

class HN(Hypernetwork):
    @staticmethod
    def from_model(state_dict, dtype=None):
        if not dtype:
            dtype = state_dict['metadata']['dtype']

        utils.cast_state_dict(state_dict, dtype)

        model = HN(state_dict)
        model.to(dtype)

        return model

class AdditionalNetworks():
    class AdditionalModule(torch.nn.Module):
        def __init__(self, name, module: torch.nn.Module):
            super().__init__()
            self.name = name
            self.original = module.forward
            self.dim = module.in_features if hasattr(module, "in_features") else None

            self.hns = []
            self.loras = []

        def forward(self, x):
            for hn in self.hns:
                x = x + hn(x)
            out = self.original(x)
            for lora in self.loras:
                out = out + lora(x)
            return out

        def attach_lora(self, module):
            self.loras.append(module)
        
        def attach_hn(self, module):
            self.hns.append(module)

        def clear(self):
            self.loras.clear()
            self.hns.clear()

    def __init__(self, model):
        self.modules = {}

        model_type = str(type(model))
        if "CLIP" in model_type:
            self.modules = self.hijack_model(model, 'te', ["CLIPAttention", "CLIPMLP"])
        elif "UNET" in model_type:
            self.modules = self.hijack_model(model, 'unet', ["Transformer2DModel", "Attention"])
        else:
            raise ValueError(f"INVALID TARGET {model_type}")

    def clear(self):
        for name in self.modules:
            self.modules[name].clear()

    def hijack_model(self, model, prefix, targets):
        modules = {}
        for module_name, module in model.named_modules():
            if module.__class__.__name__ in targets:
                for child_name, child_module in module.named_modules():
                    child_class = child_module.__class__.__name__
                    if child_class == "Linear" or (child_class == "Conv2d" and child_module.kernel_size == (1, 1)):
                        name = (prefix + '.' + module_name + '.' + child_name).replace('.', '_')
                        modules[name] = AdditionalNetworks.AdditionalModule(name, child_module)
                        child_module.forward = modules[name].forward
        return modules