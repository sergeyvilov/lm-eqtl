import torch

import numpy as np

from torch import nn

#from tqdm.notebook import tqdm
from tqdm import tqdm

from helpers.metrics import MeanRecall, MaskedAccuracy, IQS

from helpers.misc import EMA, print_class_recall

from torch.nn.functional import log_softmax

def model_train(model, optimizer, dataloader, device, silent=False):

    criterion = torch.nn.CrossEntropyLoss(reduction = "mean")

    accuracy = MaskedAccuracy(smooth=True).to(device)
    masked_recall = MeanRecall().to(device)
    masked_accuracy = MaskedAccuracy(smooth=True).to(device)
    masked_IQS = IQS().to(device)
    
    model.train() #model to train mode

    if not silent:
        tot_itr = len(dataloader.dataset)//dataloader.batch_size #total train iterations
        pbar = tqdm(total = tot_itr, ncols=750) #progress bar

    loss_EMA = EMA()

    for itr_idx, ((masked_sequence, species_label), targets_masked, targets, _) in enumerate(dataloader):

        masked_sequence = masked_sequence.to(device)
        species_label = species_label.to(device)
        targets_masked = targets_masked.to(device)
        targets = targets.to(device)

        logits, _ = model(masked_sequence, species_label)

        loss = criterion(logits, targets_masked)

        optimizer.zero_grad()

        loss.backward()

        #if max_abs_grad:
        #    torch.nn.utils.clip_grad_value_(model.parameters(), max_abs_grad)

        optimizer.step()

        smoothed_loss = loss_EMA.update(loss.item())

        preds = torch.argmax(logits, dim=1)

        accuracy.update(preds, targets)
        masked_recall.update(preds, targets_masked)
        masked_accuracy.update(preds, targets_masked)
        masked_IQS.update(preds, targets_masked)
        
        if not silent:

            pbar.update(1)
            pbar.set_description(f'acc: {accuracy.compute():.4}, {print_class_recall(masked_recall.compute(), "masked recall: ")}, masked acc: {masked_accuracy.compute():.4}, masked IQS: {masked_IQS.compute():.4}, loss: {smoothed_loss:.4}')

    if not silent:
        del pbar

    return smoothed_loss, accuracy.compute(), masked_accuracy.compute(), masked_recall.compute(), masked_IQS.compute()


def model_eval(model, optimizer, dataloader, device, get_embeddings = False, temperature=None, silent=False):

    criterion = torch.nn.CrossEntropyLoss(reduction = "mean")
    
    accuracy = MaskedAccuracy().to(device)
    masked_recall = MeanRecall().to(device)
    masked_accuracy = MaskedAccuracy().to(device)
    masked_IQS = IQS().to(device)

    model.eval() #model to train mode

    if not silent:
        tot_itr = len(dataloader.dataset)//dataloader.batch_size #total train iterations
        pbar = tqdm(total = tot_itr, ncols=750) #progress bar

    avg_loss = 0.

    all_embeddings = []

    motif_probas = []

    with torch.no_grad():

        for itr_idx, ((masked_sequence, species_label), targets_masked, targets, seq) in enumerate(dataloader):

            if get_embeddings:
                #batches are generated by transformation in the dataset,
                #so remove extra batch dimension added by dataloader
                masked_sequence, targets_masked, targets = masked_sequence[0], targets_masked[0], targets[0]
                species_label = species_label.tile((len(masked_sequence),))

            masked_sequence = masked_sequence.to(device)
            species_label = species_label.long().to(device)
            targets_masked = targets_masked.to(device)
            targets = targets.to(device)
            
            logits, embeddings = model(masked_sequence, species_label)

            if temperature:
                logits /= temperature

            loss = criterion(logits, targets_masked)

            avg_loss += loss.item()

            preds = torch.argmax(logits, dim=1)

            accuracy.update(preds, targets)
            masked_recall.update(preds, targets_masked)
            masked_accuracy.update(preds, targets_masked)
            masked_IQS.update(preds, targets_masked)

            if  get_embeddings:

                seq_name = dataloader.dataset.seq_df.iloc[itr_idx].seq_name

                # only get embeddings of the masked nucleotide
                sequence_embedding = embeddings["seq_embedding"]
                sequence_embedding = sequence_embedding.transpose(-1,-2)[targets_masked!=-100]
                # shape # B, L, dim  to L,dim, left with only masked nucleotide embeddings
                # average over sequence
                #print(sequence_embedding.shape)
                sequence_embedding = sequence_embedding.mean(dim=0) # if we mask
                #sequence_embedding = sequence_embedding[0].mean(dim=-1) # no mask

                sequence_embedding = sequence_embedding.detach().cpu().numpy()

                logits = torch.permute(logits,(2,0,1)).reshape(-1,masked_sequence.shape[1]).detach()

                targets_masked = targets_masked.T.flatten()

                masked_targets = targets_masked[targets_masked!=-100].cpu()
                logits = logits[targets_masked!=-100].cpu()

                logprobs = log_softmax(logits, dim=1).numpy()

                #mapping = {'A':0,'C':1,'G':2,'T':3}
                #ground_truth_logprobs = np.array([logprobs[idx,mapping[base]] for idx,base in enumerate(seq[0])])

                ground_truth_logprobs = np.array([logprobs[idx,base] for idx,base in enumerate(masked_targets)])

                all_embeddings.append((seq_name,sequence_embedding,ground_truth_logprobs))

            if not silent:

                pbar.update(1)
                pbar.set_description(f'acc: {accuracy.compute():.4}, {print_class_recall(masked_recall.compute(), "masked recall: ")}, masked acc: {masked_accuracy.compute():.4}, masked IQS: {masked_IQS.compute():.4}, loss: {avg_loss/(itr_idx+1):.4}')

    if not silent:
        del pbar

    return (avg_loss/(itr_idx+1), accuracy.compute(), masked_accuracy.compute(), masked_recall.compute(), masked_IQS.compute()), all_embeddings, motif_probas