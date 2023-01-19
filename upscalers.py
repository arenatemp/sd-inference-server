import torch
import utils
import PIL

from basicsr.archs.rrdbnet_arch import RRDBNet

def upscale_latent(latents, mode, factor):
    latents = torch.nn.functional.interpolate(latents.clone().detach(), scale_factor=(factor,factor), mode=mode)
    return latents

def upscale_pixel(images, mode, factor):
    images = [i for i in images]
    for i in range(len(images)):
        w,h = images[i].size
        w,h = int(w*factor), int(h*factor)
        images[i] = images[i].resize((w,h), mode)
    return images

def upscale_super_resolution(images, model, factor):
    images = [i for i in images]
    sizes = [(int(factor*i.size[0]),int(factor*i.size[1])) for i in images]

    with torch.inference_mode():
        for i in range(len(images)):
            while images[i].size[0] < sizes[i][0]:
                img = utils.TO_TENSOR(images[i]).unsqueeze(0)
                img = img.to(model.device)
                out = model(img)
                out = out.cpu().clamp_(0, 1)
                images[i] = utils.FROM_TENSOR(out.squeeze(0))
            if images[i].size != sizes[i]:
                images[i] = images[i].resize(sizes[i], resample=PIL.Image.LANCZOS)
    
    return images

class SR(RRDBNet):
    @staticmethod
    def from_model(state_dict, dtype=None):
        if len(state_dict) == 1:
            state_dict = next(iter(state_dict.values()))

        if "model.0.weight" in state_dict:
            state_dict = SR.convert(state_dict)
        
        num_block = max([int(k.split(".")[1]) for k in state_dict if k.startswith("body.")] + [0]) + 1

        if num_block == 0:
            raise ValueError(f"ERROR unknown upscaler format")

        model = SR(3, 3, 4, 64, num_block)
        model.load_state_dict(state_dict)

        return model
    
    @staticmethod
    def convert(state_dict):
        for k in list(state_dict.keys()):
            kk = k.lower()
            kk = kk.replace("model.1.sub.", "body.")
            kk = kk.replace(".0.weight", ".weight")
            kk = kk.replace(".0.bias", ".bias")
            if k != kk:
                state_dict[kk] = state_dict[k]
                del state_dict[k]

        KEYS = {
            "model.": "conv_first.",
            "model.3.": "conv_up1.",
            "model.6.": "conv_up2.",
            "model.8.": "conv_hr.",
            "body.23.": "conv_body.",
            "model.10.": "conv_last.",
        }

        for k in KEYS:
            for s in ["weight", "bias"]:
                state_dict[KEYS[k]+s] = state_dict[k+s]
                del state_dict[k+s]
        
        return state_dict

    def __getattr__(self, name):
        if name == "device":
            return next(self.parameters()).device
        return super().__getattr__(name)