import functools
import torch
import torch.nn as nn
from torch.nn import init

import models.modules.architecture as arch
import models.modules.sft_arch as sft_arch
import DTE.DTEnet as DTEnet

####################
# initialize
####################


def weights_init_normal(m, std=0.02):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        init.normal_(m.weight.data, 0.0, std)
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('Linear') != -1:
        init.normal_(m.weight.data, 0.0, std)
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('BatchNorm2d') != -1:
        init.normal_(m.weight.data, 1.0, std)  # BN also uses norm
        init.constant_(m.bias.data, 0.0)


def weights_init_kaiming(m, scale=1):
    if 'filter_layer' in m.__dict__ and m.__getattribute__('filter_layer'):
        return
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        init.kaiming_normal_(m.weight.data, a=0, mode='fan_in')
        m.weight.data *= scale
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('Linear') != -1:
        init.kaiming_normal_(m.weight.data, a=0, mode='fan_in')
        m.weight.data *= scale
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('BatchNorm2d') != -1:
        init.constant_(m.weight.data, 1.0)
        init.constant_(m.bias.data, 0.0)


def weights_init_orthogonal(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        init.orthogonal_(m.weight.data, gain=1)
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('Linear') != -1:
        init.orthogonal_(m.weight.data, gain=1)
        if m.bias is not None:
            m.bias.data.zero_()
    elif classname.find('BatchNorm2d') != -1:
        init.constant_(m.weight.data, 1.0)
        init.constant_(m.bias.data, 0.0)


def init_weights(net, init_type='kaiming', scale=1, std=0.02):
    # scale for 'kaiming', std for 'normal'.
    print('initialization method [{:s}]'.format(init_type))
    if init_type == 'normal':
        weights_init_normal_ = functools.partial(weights_init_normal, std=std)
        net.apply(weights_init_normal_)
    elif init_type == 'kaiming':
        weights_init_kaiming_ = functools.partial(weights_init_kaiming, scale=scale)
        net.apply(weights_init_kaiming_)
    elif init_type == 'orthogonal':
        net.apply(weights_init_orthogonal)
    else:
        raise NotImplementedError('initialization method [{:s}] not implemented'.format(init_type))


####################
# define network
####################


# Generator
def define_G(opt,DTE=None):
    gpu_ids = opt['gpu_ids']
    opt_net = opt['network_G']
    which_model = opt_net['which_model_G']
    opt_net['latent_input'] = opt_net['latent_input'] if opt_net['latent_input']!="None" else None
    # if opt['network_G']['DTE_arch']:#Prevent a bug when using DTE, due to inv_hTh tensor residing on a different device (GPU) than the input tensor
    #     import os
    #     os.environ["CUDA_VISIBLE_DEVICES"] = "0"

    if which_model == 'sr_resnet':  # SRResNet
        netG = arch.SRResNet(in_nc=opt_net['in_nc'], out_nc=opt_net['out_nc'], nf=opt_net['nf'], \
            nb=opt_net['nb'], upscale=opt_net['scale'], norm_type=opt_net['norm_type'], \
            act_type='relu', mode=opt_net['mode'], upsample_mode='pixelshuffle')

    elif which_model == 'sft_arch':  # SFT-GAN
        netG = sft_arch.SFT_Net()

    elif which_model == 'RRDB_net':  # RRDB
        netG = arch.RRDBNet(in_nc=opt_net['in_nc'], out_nc=opt_net['out_nc'], nf=opt_net['nf'],
            nb=opt_net['nb'], gc=opt_net['gc'], upscale=opt_net['scale'], norm_type=opt_net['norm_type'],
            act_type='leakyrelu', mode=opt_net['mode'], upsample_mode='upconv',latent_input=opt_net['latent_input'])
    else:
        raise NotImplementedError('Generator model [{:s}] not recognized'.format(which_model))
    if opt['network_G']['DTE_arch']:
        netG = DTE.WrapArchitecture_PyTorch(netG,opt['datasets']['train']['HR_size'] if opt['is_train'] else None)
    if opt['is_train']:
        init_weights(netG, init_type='kaiming', scale=0.1)
    if gpu_ids:
        assert torch.cuda.is_available()
        netG = nn.DataParallel(netG)
    return netG


# Discriminator
def define_D(opt,DTE=None):
    gpu_ids = opt['gpu_ids']
    opt_net = opt['network_D']
    which_model = opt_net['which_model_D']
    input_patch_size = opt['datasets']['train']['HR_size']
    # in_nc = opt_net['in_nc']*(2 if opt['network_D']['decomposed_input'] else 1)
    in_nc = opt_net['in_nc']
    assert not ((opt_net['pre_clipping'] or opt_net['decomposed_input']) and which_model!='PatchGAN'),'Unsupported yet'
    if DTE is not None:
        input_patch_size -= 2*DTE.invalidity_margins_HR
    if which_model == 'discriminator_vgg_128':
        netD = arch.Discriminator_VGG_128(in_nc=in_nc, base_nf=opt_net['nf'], \
            norm_type=opt_net['norm_type'], mode=opt_net['mode'], act_type=opt_net['act_type'],input_patch_size=input_patch_size)

    elif which_model == 'dis_acd':  # sft-gan, Auxiliary Classifier Discriminator
        netD = sft_arch.ACD_VGG_BN_96()
    elif which_model=='PatchGAN':
        norm_layer = functools.partial(nn.InstanceNorm2d, affine=False, track_running_stats=False)
        netD = arch.PatchGAN_Discriminator(input_nc=in_nc, opt_net=opt_net,ndf=opt_net['nf'], n_layers=opt_net['n_layers'], norm_layer=norm_layer)
    elif which_model == 'discriminator_vgg_96':
        netD = arch.Discriminator_VGG_96(in_nc=in_nc, base_nf=opt_net['nf'], \
            norm_type=opt_net['norm_type'], mode=opt_net['mode'], act_type=opt_net['act_type'])
    elif which_model == 'discriminator_vgg_192':
        netD = arch.Discriminator_VGG_192(in_nc=in_nc, base_nf=opt_net['nf'], \
            norm_type=opt_net['norm_type'], mode=opt_net['mode'], act_type=opt_net['act_type'])
    elif which_model == 'discriminator_vgg_128_SN':
        netD = arch.Discriminator_VGG_128_SN()
    else:
        raise NotImplementedError('Discriminator model [{:s}] not recognized'.format(which_model))

    init_weights(netD, init_type='kaiming', scale=1)
    if gpu_ids:
        netD = nn.DataParallel(netD)
    return netD


def define_F(opt, use_bn=False):
    gpu_ids = opt['gpu_ids']
    device = torch.device('cuda' if gpu_ids else 'cpu')
    # pytorch pretrained VGG19-54, before ReLU.
    if use_bn:
        feature_layer = 49
    else:
        feature_layer = 34
    netF = arch.VGGFeatureExtractor(feature_layer=feature_layer, use_bn=use_bn, \
        use_input_norm=True, device=device)
    # netF = arch.ResNet101FeatureExtractor(use_input_norm=True, device=device)
    if gpu_ids:
        netF = nn.DataParallel(netF)
    netF.eval()  # No need to train
    return netF

def define_E(input_nc, output_nc, ndf, net_type,init_type='xavier', init_gain=0.02, gpu_ids=[], vaeLike=False):#,norm='batch', nl='lrelu'):
    netE = None
    norm_layer = functools.partial(nn.InstanceNorm2d, affine=False, track_running_stats=False)
    nl = 'lrelu'  # use leaky relu for E
    nl_layer = functools.partial(nn.LeakyReLU, negative_slope=0.2, inplace=True)
    if net_type == 'resnet_128':
        netE = arch.E_ResNet(input_nc, output_nc, ndf, n_blocks=4, norm_layer=norm_layer,
                       nl_layer=nl_layer, vaeLike=vaeLike)
    elif net_type == 'resnet_256':
        netE = arch.E_ResNet(input_nc, output_nc, ndf, n_blocks=5, norm_layer=norm_layer,
                       nl_layer=nl_layer, vaeLike=vaeLike)
    elif net_type == 'conv_128':
        netE = arch.E_NLayers(input_nc, output_nc, ndf, n_layers=4, norm_layer=norm_layer,
                        nl_layer=nl_layer, vaeLike=vaeLike)
    elif net_type == 'conv_256':
        netE = arch.E_NLayers(input_nc, output_nc, ndf, n_layers=5, norm_layer=norm_layer,
                        nl_layer=nl_layer, vaeLike=vaeLike)
    else:
        raise NotImplementedError('Encoder model name [%s] is not recognized' % net_type)
    init_weights(netE, init_type='kaiming', scale=1)
    if gpu_ids:
        assert torch.cuda.is_available()
        netE = nn.DataParallel(netE)
    return netE
    # return init_net(netE, init_type, init_gain, gpu_ids)
